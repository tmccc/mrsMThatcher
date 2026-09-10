from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrsMThatcher2 as bot
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401


DEPENDENCIES = {'remote_write_transport_journal_paths': ['CONFIRMED_REPLY_RECEIPT_FILE',
                                          'HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE',
                                          'MEME_POST_RECEIPT_FILE',
                                          'REGULAR_POST_RECEIPT_FILE',
                                          'journal_path_for_receipt'],
 'remote_source_receipt_paths': ['CONFIRMED_REPLY_RECEIPT_FILE',
                                 'HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE',
                                 'MEME_POST_RECEIPT_FILE',
                                 'REGULAR_POST_RECEIPT_FILE'],
 'canonical_transport_receipt_path_for_lane': ['CONFIRMED_REPLY_RECEIPT_FILE',
                                               'HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE',
                                               'MEME_POST_RECEIPT_FILE',
                                               'REGULAR_POST_RECEIPT_FILE'],
 'transport_source_semantic_validator': ['main_post_attempt_binds_payload',
                                         'sending_reply_receipt_is_semantically_valid'],
 '_legacy_conversational_transport_source_semantic_validator': ['_legacy_sending_reply_receipt_is_semantically_valid'],
 'bind_lane_transport_source': ['TRANSPORT_SOURCE_VALIDATOR_ID',
                                'bind_transport_source',
                                'canonical_atomic_json_bytes',
                                'transport_source_semantic_validator'],
 'block_if_unrelated_receipt_appeared_for_tweet_transport': ['CONFIRMED_REPLY_RECEIPT_FILE',
                                                             'HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE',
                                                             'MEDIA_UPLOAD_RECEIPT_FILE',
                                                             'MEME_POST_RECEIPT_FILE',
                                                             'REGULAR_POST_RECEIPT_FILE',
                                                             'TransportJournalError',
                                                             'media_upload_receipt_is_blocking',
                                                             'receipt_namespace_entry_exists',
                                                             'remote_receipt_retirement_is_blocking'],
 'block_if_unrelated_receipt_appeared_for_media_transport': ['CONFIRMED_REPLY_RECEIPT_FILE',
                                                             'HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE',
                                                             'MEME_POST_RECEIPT_FILE',
                                                             'MediaUploadReceiptError',
                                                             'REGULAR_POST_RECEIPT_FILE',
                                                             'receipt_namespace_entry_exists',
                                                             'remote_receipt_retirement_is_blocking',
                                                             'remote_write_transport_journal_is_blocking'],
 'prepare_main_tweet_transport': ['TransportJournalError',
                                  'begin_transport_transaction',
                                  'bind_lane_transport_source',
                                  'current_main_post_attempt_is_semantically_valid',
                                  'main_post_attempt_path',
                                  'main_post_attempt_payload',
                                  'mark_main_post_attempt_attempting'],
 'confirmed_media_upload_experiment_envelope': ['ConfirmedMediaUpload',
                                                'MediaUploadReceiptError',
                                                'Path',
                                                'copy',
                                                'inspect_media_upload_receipt',
                                                'validate_media_upload_payload_metadata']}

SIGNATURES = {'remote_write_transport_journal_paths': "() -> 'tuple[Path, ...]'",
 'remote_source_receipt_paths': "() -> 'tuple[Path, ...]'",
 'canonical_transport_receipt_path_for_lane': "(lane: 'str') -> 'Path | None'",
 'transport_source_semantic_validator': "(lane: 'str', receipt: 'dict', payload: 'dict') -> 'bool'",
 '_legacy_conversational_transport_source_semantic_validator': "(lane: 'str', receipt: 'dict', payload: "
                                                               "'dict') -> 'bool'",
 'bind_lane_transport_source': "(*, receipt_path: 'Path', receipt: 'dict', lane: 'str', payload: "
                               "'dict') -> 'SourceReceiptBinding'",
 'block_if_unrelated_receipt_appeared_for_tweet_transport': "(expected_receipt_path: 'Path') -> 'None'",
 'block_if_unrelated_receipt_appeared_for_media_transport': "() -> 'None'",
 'prepare_main_tweet_transport': "(attempt: 'dict') -> 'tuple[dict, SourceReceiptBinding, "
                                 "TransportAuthority]'",
 'confirmed_media_upload_experiment_envelope': "(confirmation: 'ConfirmedMediaUpload') -> 'dict | None'"}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Transport source preparation import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox', 'transaction_mutation_authority', 'remote_write_transport_journal', 'remote_media_upload_receipt'} or name.startswith('mrs_bot_') and name != 'mrs_bot_transport_source_preparation':
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
import mrs_bot_transport_source_preparation
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
assert 'transaction_mutation_authority' not in sys.modules
assert mrs_bot_transport_source_preparation.bind_lane_transport_source.__annotations__['return'] == 'SourceReceiptBinding'
assert mrs_bot_transport_source_preparation.prepare_main_tweet_transport.__annotations__['return'] == 'tuple[dict, SourceReceiptBinding, TransportAuthority]'
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

            patch.setattr(bot, "_transport_source_preparation", SimpleNamespace(**{name: capture}))
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
            patch.setattr(bot, "_transport_source_preparation", SimpleNamespace(**{name: Mock(side_effect=failure)}))
            with pytest.raises(TypeError) as caught:
                adapter(**provided)
            assert caught.value is failure


def test_path_collections_keep_order_duplicates_references_and_sorted_journals(monkeypatch):
    paths = [object(), object(), object(), object()]
    names = ("REGULAR_POST_RECEIPT_FILE", "MEME_POST_RECEIPT_FILE",
             "CONFIRMED_REPLY_RECEIPT_FILE", "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE")
    paths[2] = paths[0]
    for name, path in zip(names, paths):
        monkeypatch.setattr(bot, name, path)
    journals = [Path("z"), Path("a"), Path("z"), Path("b")]
    journal = Mock(side_effect=journals)
    monkeypatch.setattr(bot, "journal_path_for_receipt", journal)
    receipts = bot.remote_source_receipt_paths()
    assert type(receipts) is tuple and all(a is b for a, b in zip(receipts, paths))
    assert len(receipts) == 4
    assert bot.remote_write_transport_journal_paths() == (Path("a"), Path("b"), Path("z"))
    assert journal.call_args_list == [call(path) for path in paths]
    assert all(c.args[0] is path for c, path in zip(journal.call_args_list, paths))
    for lane, path in zip(("quote_image", "daily_meme", "conversational_reply", "historical_context_reply"), paths):
        value = Mock()
        value.__str__ = Mock(return_value=lane)
        assert bot.canonical_transport_receipt_path_for_lane(value) is path
        value.__str__.assert_called_once_with()
    assert bot.canonical_transport_receipt_path_for_lane(" quote_image ") is None
    failure = OSError("journal collection")
    journal.side_effect = failure
    with pytest.raises(OSError) as caught:
        bot.remote_write_transport_journal_paths()
    assert caught.value is failure


def test_canonical_lane_keeps_native_conversion_and_hash_errors():
    class BadString:
        def __str__(self):
            raise failure

    failure = ValueError("lane conversion")
    with pytest.raises(ValueError) as caught:
        bot.canonical_transport_receipt_path_for_lane(BadString())
    assert caught.value is failure

    class UnhashableString(str):
        __hash__ = None

        def __str__(self):
            return self

    with pytest.raises(TypeError, match="unhashable"):
        bot.canonical_transport_receipt_path_for_lane(UnhashableString("quote_image"))


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_main_source_short_circuits_and_returns_bool_with_original_payload(monkeypatch, lane):
    payload = object()
    receipt = {"lane": lane, "lifecycle_state": "attempting"}
    binder = Mock(return_value=["truthy"])
    monkeypatch.setattr(bot, "main_post_attempt_binds_payload", binder)
    assert bot.transport_source_semantic_validator(lane, receipt, payload) is True
    assert binder.call_args.args[0] is receipt and binder.call_args.args[1] is payload
    binder.return_value = []
    assert bot.transport_source_semantic_validator(lane, receipt, payload) is False
    binder.reset_mock()
    assert bot.transport_source_semantic_validator(lane, {"lane": "other"}, payload) is False
    assert bot.transport_source_semantic_validator(lane, {"lane": lane, "lifecycle_state": "sending"}, payload) is False
    binder.assert_not_called()
    failure = TypeError("payload binder")
    binder.side_effect = failure
    with pytest.raises(TypeError) as caught:
        bot.transport_source_semantic_validator(lane, receipt, payload)
    assert caught.value is failure
    assert bot.transport_source_semantic_validator("unknown", object(), object()) is False
    with pytest.raises(TypeError):
        bot.transport_source_semantic_validator([], receipt, payload)


@pytest.mark.parametrize("legacy", [False, True])
def test_conversational_source_keeps_ai_identity_exact_keys_and_validation_order(monkeypatch, legacy):
    trace = Mock()

    class Payload(dict):
        def get(self, key, *default):
            trace.payload_get(key)
            return super().get(key, *default)

        def __iter__(self):
            trace.payload_iter()
            return super().__iter__()

    class Receipt(dict):
        def get(self, key, *default):
            trace.receipt_get(key)
            return super().get(key, *default)

    class Target:
        def __str__(self):
            trace.target_str()
            return "42"

    name = ("_legacy_conversational_transport_source_semantic_validator" if legacy
            else "transport_source_semantic_validator")
    dependency = ("_legacy_sending_reply_receipt_is_semantically_valid" if legacy
                  else "sending_reply_receipt_is_semantically_valid")
    validator = getattr(bot, name)
    monkeypatch.setattr(bot, dependency, trace.validate)
    receipt = Receipt(reply_text="reply", target_id=Target())
    payload = Payload(text="reply", reply={"in_reply_to_tweet_id": "42"})
    trace.validate.return_value = ["valid"]
    assert validator("conversational_reply", receipt, payload) is True
    assert trace.mock_calls == [
        call.payload_get("made_with_ai"), call.validate(receipt), call.payload_iter(),
        call.payload_get("text"), call.receipt_get("reply_text"),
        call.payload_get("reply"), call.receipt_get("target_id"), call.target_str(),
    ]
    assert trace.validate.call_args.args[0] is receipt
    for ai, expected in [(True, True), (1, False), (False, False), (None, False)]:
        payload["made_with_ai"] = ai
        assert validator("conversational_reply", receipt, payload) is expected
    payload.pop("made_with_ai")
    for extra in ({"extra": 1}, {"text": "changed"}, {"reply": {"in_reply_to_tweet_id": 42}}):
        assert validator("conversational_reply", receipt, Payload(payload | extra)) is False
    trace.reset_mock()
    trace.validate.return_value = False
    assert validator("conversational_reply", receipt, payload) is False
    assert trace.mock_calls == [call.payload_get("made_with_ai"), call.validate(receipt)]
    if legacy:
        assert validator("quote_image", object(), object()) is False
    failure = OSError("current receipt validator")
    trace.validate.side_effect = failure
    with pytest.raises(OSError) as caught:
        validator("conversational_reply", receipt, payload)
    assert caught.value is failure


def test_historical_source_and_binding_use_function_local_current_formatter(monkeypatch):
    receipt = {"reply_text": "context", "parent_post_id": 42}
    payload = {"text": "context", "reply": {"in_reply_to_tweet_id": "42"}}
    for _ in range(2):
        validate = Mock(return_value=True)
        canonical = Mock(return_value=object())
        formatter = SimpleNamespace(HistoricalContextReplyStore=SimpleNamespace(_valid_sending_receipt=validate),
                                    canonical_json_bytes=canonical)
        monkeypatch.setitem(sys.modules, "historical_context_formatter", formatter)
        assert bot.transport_source_semantic_validator("historical_context_reply", receipt, payload) is True
        assert validate.call_args.args[0] is receipt
        for changed in (payload | {"made_with_ai": True}, payload | {"text": "other"},
                        payload | {"reply": {"in_reply_to_tweet_id": 42}}):
            assert bot.transport_source_semantic_validator("historical_context_reply", receipt, changed) is False
        validate.return_value = False
        assert bot.transport_source_semantic_validator("historical_context_reply", receipt, object()) is False
        binding, path, validator_id, callback = object(), object(), object(), object()
        bind = Mock(return_value=binding)
        monkeypatch.setattr(bot, "bind_transport_source", bind)
        monkeypatch.setattr(bot, "TRANSPORT_SOURCE_VALIDATOR_ID", validator_id)
        # Restore the public adapter after proving the current callback reference.
        with monkeypatch.context() as patch:
            patch.setattr(bot, "transport_source_semantic_validator", callback)
            patch.setattr(bot, "canonical_atomic_json_bytes", Mock(side_effect=AssertionError("historical bytes")))
            assert bot.bind_lane_transport_source(receipt_path=path, receipt=receipt,
                                                  lane="historical_context_reply", payload=payload) is binding
        canonical.assert_called_once_with(receipt)
        assert canonical.call_args.args[0] is receipt
        expected = dict(receipt_path=path, expected_receipt=receipt,
                        expected_receipt_bytes=canonical.return_value, lane="historical_context_reply",
                        payload=payload, validator_id=validator_id, validator=callback)
        assert bind.call_args.kwargs.keys() == expected.keys()
        assert all(bind.call_args.kwargs[key] is value for key, value in expected.items())


def test_nonhistorical_binding_keeps_current_bytes_lane_references_and_native_failures(monkeypatch):
    monkeypatch.setitem(sys.modules, "historical_context_formatter", None)
    for _ in range(2):
        receipt, path, payload, lane, encoded, binding, validator_id, callback = [object() for _ in range(8)]
        canonical, bind = Mock(return_value=encoded), Mock(return_value=binding)
        monkeypatch.setattr(bot, "canonical_atomic_json_bytes", canonical)
        monkeypatch.setattr(bot, "bind_transport_source", bind)
        monkeypatch.setattr(bot, "TRANSPORT_SOURCE_VALIDATOR_ID", validator_id)
        monkeypatch.setattr(bot, "transport_source_semantic_validator", callback)
        args = dict(receipt_path=path, receipt=receipt, lane=lane, payload=payload)
        assert bot.bind_lane_transport_source(**args) is binding
        assert canonical.call_args.args == (receipt,)
        expected = dict(receipt_path=path, expected_receipt=receipt, expected_receipt_bytes=encoded,
                        lane=lane, payload=payload, validator_id=validator_id, validator=callback)
        assert bind.call_args.kwargs.keys() == expected.keys()
        assert all(bind.call_args.kwargs[key] is value for key, value in expected.items())
        failure = ValueError("source binding")
        for dependency in (bind, canonical):
            dependency.side_effect = failure
            with pytest.raises(ValueError) as caught:
                bot.bind_lane_transport_source(**args)
            assert caught.value is failure
            dependency.side_effect = None


def _gate_trace(monkeypatch, tmp_path, lane):
    trace = Mock()
    paths = tuple(tmp_path / name for name in ("regular", "meme", "reply", "context"))
    for name, path in zip(("REGULAR_POST_RECEIPT_FILE", "MEME_POST_RECEIPT_FILE",
                           "CONFIRMED_REPLY_RECEIPT_FILE", "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE"), paths):
        monkeypatch.setattr(bot, name, path)
    for dependency, name in (("remote_receipt_retirement_is_blocking", "retirement"),
                             ("remote_write_transport_journal_is_blocking", "journal"),
                             ("receipt_namespace_entry_exists", "namespace"),
                             ("media_upload_receipt_is_blocking", "media")):
        getattr(trace, name).return_value = False
        monkeypatch.setattr(bot, dependency, getattr(trace, name))
    media = tmp_path / "media"
    monkeypatch.setattr(bot, "MEDIA_UPLOAD_RECEIPT_FILE", media)
    if lane == "tweet":
        # Equal pathname, distinct object: equality still skips the owning entry.
        run = lambda: bot.block_if_unrelated_receipt_appeared_for_tweet_transport(Path(str(paths[1])))
        order = [call.retirement(), *(call.namespace(p) for i, p in enumerate(paths) if i != 1), call.media(media)]
    else:
        run = bot.block_if_unrelated_receipt_appeared_for_media_transport
        order = [call.retirement(), call.journal(), *(call.namespace(p) for p in paths)]
    return trace, paths, run, order


@pytest.mark.parametrize("lane", ["tweet", "media"])
def test_final_gates_keep_retirement_journal_namespace_and_media_order(monkeypatch, tmp_path, lane):
    trace, paths, run, order = _gate_trace(monkeypatch, tmp_path, lane)
    assert run() is None
    assert trace.mock_calls == order
    inspected = [p for i, p in enumerate(paths) if lane == "media" or i != 1]
    assert all(c.args[0] is p for c, p in zip(trace.namespace.call_args_list, inspected))
    error = bot.TransportJournalError if lane == "tweet" else bot.MediaUploadReceiptError
    for index, expected in enumerate(order):
        trace.reset_mock()
        for name in ("retirement", "journal", "namespace", "media"):
            getattr(trace, name).side_effect = None
            getattr(trace, name).return_value = False
        name, args, _ = expected
        if name == "namespace":
            trace.namespace.side_effect = lambda path: path == args[0]
        else:
            getattr(trace, name).return_value = True
        with pytest.raises(error) as caught:
            run()
        message = {
            "retirement": f"a source-receipt retirement appeared before {lane} transport",
            "journal": "a public-create transport journal appeared before media upload",
            "namespace": f"an unrelated durable receipt appeared before {'tweet transport' if lane == 'tweet' else 'media upload'}",
            "media": "an unresolved media receipt appeared before tweet transport",
        }[name]
        assert str(caught.value) == message and caught.value.__cause__ is None
        assert trace.mock_calls == order[:index + 1]


@pytest.mark.parametrize("lane", ["tweet", "media"])
def test_final_gate_catches_only_namespace_call_oserror(monkeypatch, tmp_path, lane):
    trace, paths, run, order = _gate_trace(monkeypatch, tmp_path, lane)
    failure = PermissionError("namespace call")
    trace.namespace.side_effect = failure
    error = bot.TransportJournalError if lane == "tweet" else bot.MediaUploadReceiptError
    with pytest.raises(error) as caught:
        run()
    assert caught.value.__cause__ is failure
    suffix = "tweet transport" if lane == "tweet" else "media upload"
    assert str(caught.value) == f"an unrelated receipt namespace could not be inspected before {suffix}"
    assert trace.mock_calls == order[:2 if lane == "tweet" else 3]
    for name in ("namespace", "retirement", "media" if lane == "tweet" else "journal"):
        trace.namespace.side_effect = None
        native = ValueError("namespace native") if name == "namespace" else OSError(name)
        getattr(trace, name).side_effect = native
        with pytest.raises(type(native)) as caught:
            run()
        assert caught.value is native
        getattr(trace, name).side_effect = None

    class BadTruth:
        def __bool__(self):
            raise failure

    trace.namespace.return_value = BadTruth()
    with pytest.raises(OSError) as caught:
        run()
    assert caught.value is failure


def _preparation_trace(monkeypatch, *, failure_at=None, alias=False):
    trace = Mock()
    failure = TypeError("native preparation failure")

    class Lane:
        def __str__(self):
            return trace.lane_str()

    class Attempt(dict):
        def get(self, key, *default):
            trace.get("original" if self is original else "attempting", key)
            return super().get(key, *default)

        def __getitem__(self, key):
            trace.item(key)
            return super().__getitem__(key)

        def clear(self):
            trace.clear()
            super().clear()
            if failure_at == "clear":
                raise failure

        def update(self, value):
            trace.update(value)
            if failure_at == "update":
                super().update(partial=True)
                raise failure
            super().update(value)

    original = Attempt(lane=Lane(), lifecycle_state="sending", nested=[])
    attempting = original if alias else Attempt(original | {"lifecycle_state": "attempting"})
    trace.validate.return_value = True

    def mark(value):
        assert value is original
        if alias:
            value["lifecycle_state"] = "attempting"
        return attempting

    trace.mark.side_effect = mark
    trace.lane_str.return_value = "daily_meme"
    path, payload, source, authority = [object() for _ in range(4)]
    trace.path.return_value, trace.payload.return_value = path, payload
    trace.bind.return_value, trace.begin.return_value = source, authority
    for name, dependency in (("validate", "current_main_post_attempt_is_semantically_valid"),
                             ("mark", "mark_main_post_attempt_attempting"),
                             ("path", "main_post_attempt_path"), ("payload", "main_post_attempt_payload"),
                             ("bind", "bind_lane_transport_source"), ("begin", "begin_transport_transaction")):
        monkeypatch.setattr(bot, dependency, getattr(trace, name))
    if failure_at in {"mark", "path", "payload", "lane_str", "bind", "begin"}:
        getattr(trace, failure_at).side_effect = failure
    order = [call.validate(original), call.get("original", "lifecycle_state"), call.mark(original),
             call.get("original" if alias else "attempting", "lifecycle_state"), call.validate(attempting),
             call.path(attempting), call.payload(attempting), call.item("lane"), call.lane_str(),
             call.bind(receipt_path=path, receipt=attempting, lane="daily_meme", payload=payload),
             call.begin(receipt_path=path, source_binding=source), call.clear(), call.update(attempting)]
    return trace, original, attempting, source, authority, failure, order


@pytest.mark.parametrize("alias", [False, True])
def test_preparation_keeps_original_mutation_after_begin_and_tuple_references(monkeypatch, alias):
    trace, attempt, attempting, source, authority, _, order = _preparation_trace(monkeypatch, alias=alias)
    nested = dict.__getitem__(attempt, "nested")
    result = bot.prepare_main_tweet_transport(attempt)
    assert trace.mock_calls == order
    assert type(result) is tuple and len(result) == 3
    assert result[0] is attempt and result[1] is source and result[2] is authority
    assert trace.mark.call_args.args[0] is attempt
    assert trace.bind.call_args.kwargs["receipt"] is attempting
    assert trace.begin.call_args.kwargs["source_binding"] is source
    assert trace.update.call_args.args[0] is attempting
    if alias:
        assert attempt == {}  # Native clear/update of the same dictionary stays observable.
    else:
        assert attempt == attempting and dict.__getitem__(attempt, "nested") is nested


@pytest.mark.parametrize("failure_at", ["mark", "path", "payload", "lane_str", "bind", "begin", "clear", "update"])
def test_preparation_native_failures_leave_exact_mutation_progress(monkeypatch, failure_at):
    trace, attempt, attempting, _, _, failure, order = _preparation_trace(monkeypatch, failure_at=failure_at)
    before = dict(attempt)
    with pytest.raises(TypeError) as caught:
        bot.prepare_main_tweet_transport(attempt)
    assert caught.value is failure
    index = next(i for i, item in enumerate(order) if item[0] == failure_at)
    assert trace.mock_calls == order[:index + 1]
    assert attempt == ({} if failure_at == "clear" else {"partial": True} if failure_at == "update" else before)
    assert dict.__getitem__(attempting, "lifecycle_state") == "attempting"


@pytest.mark.parametrize("gate", ["first_validator", "sending", "attempting", "second_validator"])
def test_preparation_validation_and_lifecycle_short_circuit_order(monkeypatch, gate):
    trace, attempt, attempting, _, _, _, order = _preparation_trace(monkeypatch)
    stop = {"first_validator": 1, "sending": 2, "attempting": 4, "second_validator": 5}[gate]
    if gate == "first_validator":
        trace.validate.return_value = False
    elif gate == "sending":
        attempt["lifecycle_state"] = "attempting"
    elif gate == "attempting":
        attempting["lifecycle_state"] = "sending"
    else:
        trace.validate.side_effect = [True, False]
    before = dict(attempt)
    with pytest.raises(bot.TransportJournalError) as caught:
        bot.prepare_main_tweet_transport(attempt)
    assert str(caught.value) == (
        "only a current-schema sending main-post attempt may prepare transport" if stop <= 2
        else "main post attempt is not transport-ready"
    )
    assert trace.mock_calls == order[:stop]
    assert attempt == before


def _media_trace(monkeypatch, mismatch=None):
    trace = Mock()

    class Confirmation:
        receipt_path = object()
        receipt_device, receipt_inode, receipt_ctime_ns = 1, 2, 3
        receipt_sha256, transaction_id, media_id = "hash", "transaction", "media"

    confirmation = Confirmation()
    metadata = {"form": {"media_type": "image/png"}}

    class Document(dict):
        def get(self, key, *default):
            trace.document_get(key)
            return super().get(key, *default)

    document = Document(transaction_id="transaction", lifecycle_state="confirmed",
                        remote_media_id="media", payload_metadata=metadata)
    attributes = {"device": 1, "inode": 2, "ctime_ns": 3, "sha256": "hash"}
    if mismatch in attributes:
        attributes[mismatch] = object()
    elif mismatch in document:
        document[mismatch] = object()

    class Snapshot:
        def __getattr__(self, name):
            trace.snapshot_get(name)
            return document if name == "document" else attributes[name]

    snapshot = Snapshot()
    trace.path.return_value = object()
    trace.inspect.return_value = None if mismatch == "missing" else snapshot
    envelope = {"binding": {"identity": ["original"]}}
    trace.validate.return_value = {"engagement_question_experiment": envelope}
    import copy

    trace.deepcopy.side_effect = copy.deepcopy
    for name, value in (("ConfirmedMediaUpload", Confirmation), ("Path", trace.path),
                        ("inspect_media_upload_receipt", trace.inspect),
                        ("validate_media_upload_payload_metadata", trace.validate),
                        ("copy", SimpleNamespace(deepcopy=trace.deepcopy))):
        monkeypatch.setattr(bot, name, value)
    return trace, confirmation, document, metadata, envelope


@pytest.mark.parametrize("mismatch", ["type", "missing", "device", "inode", "ctime_ns", "sha256",
                                      "transaction_id", "lifecycle_state", "remote_media_id"])
def test_media_envelope_keeps_current_type_and_ordered_generation_refusal(monkeypatch, mismatch):
    trace, confirmation, _, _, _ = _media_trace(monkeypatch, mismatch)
    with pytest.raises(bot.MediaUploadReceiptError) as caught:
        bot.confirmed_media_upload_experiment_envelope(object() if mismatch == "type" else confirmation)
    assert str(caught.value) == ("confirmed media identity is invalid" if mismatch == "type"
                                 else "confirmed media receipt changed before main-post handoff")
    order = [call.path(confirmation.receipt_path), call.inspect(trace.path.return_value)]
    fields = ["device", "inode", "ctime_ns", "sha256", "transaction_id", "lifecycle_state", "remote_media_id"]
    if mismatch in fields:
        for field in fields[:fields.index(mismatch) + 1]:
            if field in fields[:4]:
                order.append(call.snapshot_get(field))
            else:
                order += [call.snapshot_get("document"), call.document_get(field)]
    assert trace.mock_calls == ([] if mismatch == "type" else order)


def test_media_envelope_retains_metadata_form_references_and_dictionary_only_deepcopy(monkeypatch):
    trace, confirmation, document, metadata, envelope = _media_trace(monkeypatch)
    result = bot.confirmed_media_upload_experiment_envelope(confirmation)
    assert result == envelope and result is not envelope
    assert result["binding"] is not envelope["binding"]
    result["binding"]["identity"].append("changed")
    assert envelope["binding"]["identity"] == ["original"]
    assert trace.path.call_args.args[0] is confirmation.receipt_path
    assert trace.inspect.call_args.args[0] is trace.path.return_value
    assert trace.validate.call_args.args[0] is metadata
    assert trace.validate.call_args.kwargs == {"form": metadata["form"]}
    assert trace.validate.call_args.kwargs["form"] is metadata["form"]
    assert trace.deepcopy.call_args.args[0] is envelope
    assert trace.mock_calls[-4:] == [call.snapshot_get("document"), call.document_get("payload_metadata"),
                                    call.validate(metadata, form=metadata["form"]), call.deepcopy(envelope)]
    for value in (None, [], "envelope", 1):
        trace.reset_mock()
        trace.validate.return_value = {"engagement_question_experiment": value}
        assert bot.confirmed_media_upload_experiment_envelope(confirmation) is None
        trace.deepcopy.assert_not_called()
    for metadata_value in (None, [], {"form": None}, {"form": []}):
        trace.reset_mock()
        document["payload_metadata"] = metadata_value
        with pytest.raises(bot.MediaUploadReceiptError, match="confirmed media receipt form is invalid"):
            bot.confirmed_media_upload_experiment_envelope(confirmation)
        trace.validate.assert_not_called()


@pytest.mark.parametrize("location,error", [("path", TypeError), ("inspect", OSError),
                                            ("validate", TypeError), ("validate", ValueError),
                                            ("validate", RuntimeError), ("deepcopy", ValueError)])
def test_media_envelope_wraps_only_metadata_type_and_value_errors(monkeypatch, location, error):
    trace, confirmation, _, _, _ = _media_trace(monkeypatch)
    failure = error("media authority")
    getattr(trace, location).side_effect = failure
    wrapped = location == "validate" and error in (TypeError, ValueError)
    with pytest.raises(bot.MediaUploadReceiptError if wrapped else error) as caught:
        bot.confirmed_media_upload_experiment_envelope(confirmation)
    if wrapped:
        assert str(caught.value) == "confirmed media receipt payload authority is invalid"
        assert caught.value.__cause__ is failure
    else:
        assert caught.value is failure

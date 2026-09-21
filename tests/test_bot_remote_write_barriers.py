"""Focused contracts for global remote-write barrier extraction."""
from __future__ import annotations

import inspect
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrsMThatcher2 as bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.reply_fixtures import unit_historical_context_sending_receipt

DEPENDENCIES = {
    'unresolved_conversational_reply_receipt_is_blocking': ['load_confirmed_reply_receipt'],
    'unresolved_main_post_attempt_is_blocking': ['load_meme_post_receipt', 'load_regular_post_receipt'],
    'remote_write_safety_incident_is_latched': ['_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN', '_AMBIGUOUS_REMOTE_POST_SEEN'],
    'remote_write_safety_protocol_is_active': ['ProtocolActivationError', 'REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE', 'inspect_protocol_activation', 'remote_receipt_retirement_is_blocking'],
    'historical_context_receipt_path_present_or_unsafe': ['HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE', 'log', 'os'],
    'historical_context_outbox_remote_attempt_is_blocking': ['HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE', 'historical_context_outbox_store', 'historical_context_reply_store', 'inspect_transport_state', 'journal_path_for_receipt', 'log'],
    'historical_context_outbox_remote_attempt_parent_for_local_reconciliation': ['historical_context_outbox_store'],
    'historical_context_receipt_parent_for_local_reconciliation': ['HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE'],
    'exact_historical_context_sending_receipt_matches': ['HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE', 'os'],
    'block_if_remote_write_safety_incident_latched': ['AMBIGUOUS_POST_OUTCOME_FILE', 'AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE', 'AmbiguousRemotePostOutcome', 'REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE', 'remote_write_safety_incident_is_latched', 'remote_write_safety_marker_path_present_or_unsafe', 'remote_write_safety_protocol_is_active'],
    'remote_write_transport_journal_is_blocking': ['remote_write_transport_journal_paths', 'transport_journal_is_blocking'],
    'remote_receipt_retirement_is_blocking': ['remote_source_receipt_paths', 'retirement_auxiliary_barrier_exists', 'retirement_ledger_is_blocking'],
    'block_if_remote_receipt_retirement_exists': ['AmbiguousRemotePostOutcome', 'remote_receipt_retirement_is_blocking'],
    'block_if_remote_write_transport_journal_exists': ['AmbiguousRemotePostOutcome', 'remote_write_transport_journal_is_blocking'],
    'confirmed_main_receipt_is_sole_local_recovery_barrier': ['CONFIRMED_REPLY_RECEIPT_FILE', 'HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE', 'MEME_POST_RECEIPT_FILE', 'REGULAR_POST_RECEIPT_FILE', 'inspect_transport_state', 'journal_path_for_receipt', 'load_meme_post_receipt', 'load_regular_post_receipt', 'log', 'receipt_namespace_entry_exists', 'remote_write_transport_journal_paths', 'transport_journal_is_blocking', 'verify_lane_transport_source_lineage_if_present'],
    'remote_media_upload_receipt_is_blocking': ['MEDIA_UPLOAD_RECEIPT_FILE', 'media_upload_receipt_is_blocking'],
    'block_if_remote_media_upload_receipt_exists': ['AmbiguousRemotePostOutcome', 'remote_media_upload_receipt_is_blocking'],
    'block_if_ambiguous_remote_post': ['AmbiguousRemotePostOutcome', 'CONFIRMED_REPLY_RECEIPT_FILE', 'HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE', 'InvalidMemePostReceipt', 'InvalidRegularPostReceipt', 'MEME_POST_RECEIPT_FILE', 'REGULAR_POST_RECEIPT_FILE', 'block_if_remote_media_upload_receipt_exists', 'block_if_remote_receipt_retirement_exists', 'block_if_remote_write_safety_incident_latched', 'block_if_remote_write_transport_journal_exists', 'confirmed_main_receipt_is_sole_local_recovery_barrier', 'current_main_post_attempt_is_semantically_valid', 'exact_historical_context_sending_receipt_matches', 'historical_context_outbox_remote_attempt_is_blocking', 'historical_context_receipt_parent_for_local_reconciliation', 'historical_context_receipt_path_present_or_unsafe', 'inspect_transport_state', 'load_confirmed_reply_receipt', 'load_meme_post_receipt', 'load_regular_post_receipt', 'remote_write_transport_journal_paths', 'transport_journal_is_blocking'],
    'ambiguous_remote_post_is_blocking': ['historical_context_outbox_remote_attempt_is_blocking', 'historical_context_receipt_path_present_or_unsafe', 'log', 'remote_media_upload_receipt_is_blocking', 'remote_receipt_retirement_is_blocking', 'remote_write_safety_incident_is_latched', 'remote_write_safety_marker_path_present_or_unsafe', 'remote_write_safety_protocol_is_active', 'remote_write_transport_journal_is_blocking', 'unresolved_conversational_reply_receipt_is_blocking', 'unresolved_main_post_attempt_is_blocking'],
}

SIGNATURES = {'unresolved_conversational_reply_receipt_is_blocking': "() -> 'bool'",
 'unresolved_main_post_attempt_is_blocking': "() -> 'bool'",
 'remote_write_safety_incident_is_latched': "() -> 'bool'",
 'remote_write_safety_protocol_is_active': "() -> 'bool'",
 'historical_context_receipt_path_present_or_unsafe': "() -> 'bool'",
 'historical_context_outbox_remote_attempt_is_blocking': "(*, prepared_receipt: 'dict | None' = "
                                                         'None, prepared_transport_authority: '
                                                         "'TransportAuthority | None' = None, "
                                                         'allow_local_reconciliation_parent_id: '
                                                         "'str | None' = None) -> 'bool'",
 'historical_context_outbox_remote_attempt_parent_for_local_reconciliation': "() -> 'str | None'",
 'historical_context_receipt_parent_for_local_reconciliation': "() -> 'str | None'",
 'exact_historical_context_sending_receipt_matches': "(receipt: 'dict | None') -> 'bool'",
 'block_if_remote_write_safety_incident_latched': "() -> 'None'",
 'remote_write_transport_journal_is_blocking': "() -> 'bool'",
 'remote_receipt_retirement_is_blocking': "() -> 'bool'",
 'block_if_remote_receipt_retirement_exists': "() -> 'None'",
 'block_if_remote_write_transport_journal_exists': "() -> 'None'",
 'confirmed_main_receipt_is_sole_local_recovery_barrier': "() -> 'bool'",
 'remote_media_upload_receipt_is_blocking': "() -> 'bool'",
 'block_if_remote_media_upload_receipt_exists': "() -> 'None'",
 'block_if_ambiguous_remote_post': "(*, prepared_conversational_reply_receipt: 'dict | None' = "
                                   "None, prepared_historical_context_reply_receipt: 'dict | None' "
                                   "= None, prepared_main_post_attempt: 'dict | None' = None, "
                                   "allow_confirmed_pending_schedule_reconciliation: 'bool' = "
                                   "False, allow_historical_context_receipt_reconciliation: 'bool' "
                                   '= False, '
                                   "allow_historical_context_outbox_reconciliation_parent_id: 'str "
                                   "| None' = None, prepared_transport_authority: "
                                   "'TransportAuthority | None' = None) -> 'None'",
 'ambiguous_remote_post_is_blocking': "() -> 'bool'"}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Remote-write barriers import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox'} or name.startswith('mrs_bot_') and name != 'mrs_bot_remote_write_barriers':
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
import mrs_bot_remote_write_barriers
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


@pytest.mark.parametrize("name", [name for name, deps in DEPENDENCIES.items() if deps])
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
                if name == "block_if_ambiguous_remote_post" and dep == "load_confirmed_reply_receipt":
                    patch.setattr(
                        bot,
                        "_reply_receipts_owner",
                        Mock(return_value=SimpleNamespace(load=value)),
                    )
                else:
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

            patch.setattr(bot, "_remote_write_barriers", SimpleNamespace(**{name: capture}))
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
            patch.setattr(bot, "_remote_write_barriers", SimpleNamespace(**{name: Mock(side_effect=failure)}))
            with pytest.raises(TypeError) as caught:
                adapter(**provided)
            assert caught.value is failure


def test_small_readers_keep_eager_main_reads_and_raw_latch_and_media_values(monkeypatch):
    reads = Mock()
    reads.regular.return_value = ("invalid", None)
    reads.meme.return_value = ("absent", None)
    monkeypatch.setattr(bot, "load_regular_post_receipt", reads.regular)
    monkeypatch.setattr(bot, "load_meme_post_receipt", reads.meme)
    assert bot.unresolved_main_post_attempt_is_blocking() is True
    assert reads.mock_calls == [call.regular(), call.meme()]
    token = object()
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", [])
    monkeypatch.setattr(bot, "_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN", token)
    assert bot.remote_write_safety_incident_is_latched() is token
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", token)
    monkeypatch.setattr(bot, "_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN", [])
    assert bot.remote_write_safety_incident_is_latched() is token
    media = Mock(return_value=token)
    monkeypatch.setattr(bot, "media_upload_receipt_is_blocking", media)
    assert bot.remote_media_upload_receipt_is_blocking() is token
    media.assert_called_once_with(bot.MEDIA_UPLOAD_RECEIPT_FILE)


@pytest.mark.parametrize("failure", [None, OSError("inspect"), bot.ProtocolActivationError("inspect"),
                                     TypeError("inspect"), KeyboardInterrupt("inspect")])
def test_protocol_inspection_keeps_narrow_catch_and_lazy_retirement(monkeypatch, failure):
    inspection = Mock(side_effect=failure)
    retirement = Mock(return_value=False)
    monkeypatch.setattr(bot, "inspect_protocol_activation", inspection)
    monkeypatch.setattr(bot, "remote_receipt_retirement_is_blocking", retirement)
    if failure is None:
        assert bot.remote_write_safety_protocol_is_active() is True
        retirement.assert_called_once_with()
        retirement.side_effect = TypeError("retirement outside inspection catch")
        with pytest.raises(TypeError, match="outside inspection catch"):
            bot.remote_write_safety_protocol_is_active()
    else:
        if isinstance(failure, (OSError, bot.ProtocolActivationError)):
            assert bot.remote_write_safety_protocol_is_active() is False
        else:
            with pytest.raises(type(failure)) as caught:
                bot.remote_write_safety_protocol_is_active()
            assert caught.value is failure
        inspection.assert_called_once_with(bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE)
        retirement.assert_not_called()


def test_namespace_failure_scope_and_lazy_journal_retirement_paths(monkeypatch):
    logger = Mock()
    probe = Mock()
    monkeypatch.setattr(bot, "log", logger)
    monkeypatch.setattr(bot, "os", SimpleNamespace(lstat=probe))
    for failure, expected in [(FileNotFoundError(), False), (PermissionError(), True)]:
        probe.side_effect = failure
        assert bot.historical_context_receipt_path_present_or_unsafe() is expected
    logger.critical.assert_called_once()
    probe.side_effect = KeyboardInterrupt("namespace")
    with pytest.raises(KeyboardInterrupt, match="namespace"):
        bot.historical_context_receipt_path_present_or_unsafe()
    events = []

    def paths():
        for path in ("first", "second", "must not inspect"):
            events.append(("yield", path))
            yield path

    monkeypatch.setattr(bot, "remote_write_transport_journal_paths", paths)
    monkeypatch.setattr(bot, "transport_journal_is_blocking",
                        lambda p: events.append(("journal", p)) or p == "second")
    assert bot.remote_write_transport_journal_is_blocking() is True
    assert events == [("yield", "first"), ("journal", "first"),
                      ("yield", "second"), ("journal", "second")]
    events.clear()
    monkeypatch.setattr(bot, "remote_source_receipt_paths", paths)
    monkeypatch.setattr(bot, "retirement_auxiliary_barrier_exists",
                        lambda p: events.append(("auxiliary", p)) or p == "second")
    monkeypatch.setattr(bot, "retirement_ledger_is_blocking",
                        lambda p: events.append(("ledger", p)) or False)
    assert bot.remote_receipt_retirement_is_blocking() is True
    assert events == [("yield", "first"), ("auxiliary", "first"), ("ledger", "first"),
                      ("yield", "second"), ("auxiliary", "second")]


@pytest.mark.parametrize("marker_present", [False, True])
def test_incident_probe_precedes_current_latch_and_marker_message_wins(monkeypatch, marker_present):
    events = []
    current_latch = bot.remote_write_safety_incident_is_latched
    monkeypatch.setattr(bot, "remote_write_safety_protocol_is_active",
                        lambda: events.append("protocol") or True)

    def probe():
        events.append("marker")
        monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", True)
        return marker_present

    monkeypatch.setattr(bot, "remote_write_safety_marker_path_present_or_unsafe", probe)
    monkeypatch.setattr(bot, "remote_write_safety_incident_is_latched",
                        lambda: events.append("latch") or current_latch())
    message = "barrier blocks further posting" if marker_present else "in-process"
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match=message):
        bot.block_if_remote_write_safety_incident_latched()
    assert events == ["protocol", "marker", "latch"]


def _boolean_checks(monkeypatch, stop=10):
    names = [
        "remote_write_safety_protocol_is_active", "remote_write_safety_incident_is_latched",
        "remote_write_safety_marker_path_present_or_unsafe", "historical_context_outbox_remote_attempt_is_blocking",
        "remote_write_transport_journal_is_blocking", "remote_media_upload_receipt_is_blocking",
        "remote_receipt_retirement_is_blocking", "historical_context_receipt_path_present_or_unsafe",
        "unresolved_main_post_attempt_is_blocking", "unresolved_conversational_reply_receipt_is_blocking",
    ]
    events, token = [], object()
    for index, name in enumerate(names):
        value = (stop != 0) if index == 0 else (token if index == stop else False)
        monkeypatch.setattr(bot, name, lambda n=name, v=value: events.append(n) or v)
    return names, events, token


@pytest.mark.parametrize("stop", range(11))
def test_boolean_barrier_keeps_its_own_order_short_circuit_and_raw_final_value(monkeypatch, stop):
    names, events, token = _boolean_checks(monkeypatch, stop)
    result = bot.ambiguous_remote_post_is_blocking()
    assert events == names[:stop + 1]
    assert result is (token if stop in (8, 9) else stop < 10)


@pytest.mark.parametrize("index, failure, caught_locally", [
    (3, TypeError("early"), False), (8, TypeError("reader"), True),
    (9, KeyboardInterrupt("reader"), False),
])
def test_boolean_barrier_catches_only_final_reader_exceptions(monkeypatch, index, failure, caught_locally):
    names, events, _token = _boolean_checks(monkeypatch)
    logger = Mock()
    monkeypatch.setattr(bot, "log", logger)
    monkeypatch.setattr(bot, names[index], Mock(side_effect=failure))
    if caught_locally:
        assert bot.ambiguous_remote_post_is_blocking() is True
        logger.critical.assert_called_once()
    else:
        with pytest.raises(type(failure)) as caught:
            bot.ambiguous_remote_post_is_blocking()
        assert caught.value is failure
        logger.critical.assert_not_called()
    assert events == names[:index]


@pytest.fixture
def prepared_outbox(monkeypatch):
    receipt = unit_historical_context_sending_receipt()
    raw = (json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    digest = bot.hashlib.sha256(raw).hexdigest()
    source = {"basename": bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.name, "sha256": digest}
    journal = bot.journal_path_for_receipt(bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE)
    authority = SimpleNamespace(lane="historical_context_reply", lifecycle_state="attempting",
                                journal_path=str(journal), journal_sha256="journal", fence_sha256="fence",
                                transaction_id="transaction", source_receipt_basename=source["basename"])
    document = {"transaction_id": authority.transaction_id, "source_receipt": source}
    state = SimpleNamespace(errors=[], classification="attempting_pair",
                            journal=SimpleNamespace(sha256="journal", document=document),
                            fence=SimpleNamespace(sha256="fence", document=dict(document)))
    obligation = {"parent_post_id": receipt["parent_post_id"], "context_reply": {
        "state": "context_reply_attempting", "remote_transaction_started": True,
        "quote_id": receipt["quote_id"], "source_receipt_sha256": digest,
        "source_receipt_attempt_number": receipt["attempt_number"],
    }}
    obligations = {"row": obligation}
    load = Mock(return_value=(receipt, raw))
    monkeypatch.setattr(bot, "historical_context_outbox_store",
                        lambda: SimpleNamespace(snapshot=lambda: {"obligations": obligations}))
    monkeypatch.setattr(bot, "historical_context_reply_store",
                        lambda: SimpleNamespace(_load_receipt_safely=load))
    monkeypatch.setattr(bot, "inspect_transport_state", Mock(return_value=state))
    return SimpleNamespace(receipt=receipt, authority=authority, state=state,
                           obligations=obligations, obligation=obligation, load=load)


@pytest.mark.parametrize("case", ["exact", "missing", "wrong_source", "wrong_fence", "legacy", "other_parent"])
def test_prepared_outbox_exception_requires_exact_source_and_observed_remote_attempt(prepared_outbox, case):
    data = prepared_outbox
    if case == "missing":
        data.obligations.clear()
    elif case == "wrong_source":
        data.obligation["context_reply"]["source_receipt_sha256"] = "other"
    elif case == "wrong_fence":
        data.state.fence.sha256 = "other"
    elif case == "legacy":
        del data.obligation["context_reply"]["remote_transaction_started"]
    elif case == "other_parent":
        data.obligations["second"] = {**data.obligation, "parent_post_id": "222"}
    assert bot.historical_context_outbox_remote_attempt_is_blocking(
        prepared_receipt=data.receipt, prepared_transport_authority=data.authority,
    ) is (case != "exact")
    if case == "missing":
        assert bot.historical_context_outbox_remote_attempt_is_blocking(
            prepared_transport_authority=data.authority,
            allow_local_reconciliation_parent_id=data.receipt["parent_post_id"],
        ) is True


def test_outbox_hash_precedes_byte_type_gate_with_original_exception_scope(monkeypatch, prepared_outbox):
    data = prepared_outbox
    bad_bytes = object()
    data.load.return_value = (data.receipt, bad_bytes)
    hashing = Mock(side_effect=TypeError("hash before type gate"))
    logger = Mock()
    monkeypatch.setattr(bot._remote_write_barriers, "hashlib", SimpleNamespace(sha256=hashing))
    monkeypatch.setattr(bot, "log", logger)
    assert bot.historical_context_outbox_remote_attempt_is_blocking(
        prepared_transport_authority=data.authority,
    ) is True
    hashing.assert_called_once_with(bad_bytes)
    logger.critical.assert_called_once()
    hashing.side_effect = KeyboardInterrupt("hash")
    with pytest.raises(KeyboardInterrupt, match="hash"):
        bot.historical_context_outbox_remote_attempt_is_blocking(prepared_transport_authority=data.authority)


def test_local_parent_selection_counts_rows_without_deduplication(prepared_outbox):
    data = prepared_outbox
    assert bot.historical_context_outbox_remote_attempt_parent_for_local_reconciliation() == "111"
    assert bot.historical_context_outbox_remote_attempt_is_blocking(allow_local_reconciliation_parent_id=111) is False
    data.obligations["duplicate"] = dict(data.obligation)
    assert bot.historical_context_outbox_remote_attempt_parent_for_local_reconciliation() is None
    data.obligations["duplicate"]["parent_post_id"] = "222"
    assert bot.historical_context_outbox_remote_attempt_is_blocking(allow_local_reconciliation_parent_id=111) is True


def test_historical_imports_are_call_time_and_schema_serialization_precede_filesystem(monkeypatch):
    monkeypatch.setitem(sys.modules, "historical_context_formatter", None)
    assert bot.exact_historical_context_sending_receipt_matches(None) is False
    with pytest.raises(ModuleNotFoundError):
        bot.historical_context_receipt_parent_for_local_reconciliation()
    filesystem = Mock(side_effect=AssertionError("filesystem before schema/canonical bytes"))
    monkeypatch.setattr(bot, "os", SimpleNamespace(lstat=filesystem))
    for _ in range(2):
        token = object()
        parent = Mock(return_value=token)
        valid = Mock(return_value=False)
        monkeypatch.setitem(sys.modules, "historical_context_formatter", SimpleNamespace(
            HistoricalContextReplyStore=SimpleNamespace(
                receipt_parent_for_safe_local_reconciliation=parent, _valid_sending_receipt=valid)))
        assert bot.historical_context_receipt_parent_for_local_reconciliation() is token
        parent.assert_called_once_with(bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE)
        assert bot.exact_historical_context_sending_receipt_matches({}) is False
        valid.return_value = True
        with pytest.raises(TypeError):
            bot.exact_historical_context_sending_receipt_matches({"unserializable": object()})
    filesystem.assert_not_called()


@pytest.mark.parametrize("failure", [None, OSError("read"), KeyboardInterrupt("read")])
def test_exact_sending_match_bounds_reads_and_closes_descriptor_on_native_failure(monkeypatch, failure):
    receipt = unit_historical_context_sending_receipt()
    raw = (json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    path = bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
    path.write_bytes(raw)
    path.chmod(0o600)
    real_os = bot.os
    read = Mock(wraps=real_os.read, side_effect=failure)
    close = Mock(wraps=real_os.close)
    fstat = Mock(wraps=real_os.fstat)
    lstat = Mock(wraps=real_os.lstat)
    opened = Mock(wraps=real_os.open)
    monkeypatch.setattr(bot, "os", SimpleNamespace(
        lstat=lstat, fstat=fstat, read=read, close=close, open=opened,
        geteuid=real_os.geteuid, O_RDONLY=real_os.O_RDONLY,
        O_NOFOLLOW=real_os.O_NOFOLLOW, O_CLOEXEC=real_os.O_CLOEXEC,
    ))
    if isinstance(failure, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt) as caught:
            bot.exact_historical_context_sending_receipt_matches(receipt)
        assert caught.value is failure
    else:
        assert bot.exact_historical_context_sending_receipt_matches(receipt) is (failure is None)
    close.assert_called_once()
    descriptor = close.call_args.args[0]
    opened.assert_called_once_with(path, real_os.O_RDONLY | real_os.O_NOFOLLOW | real_os.O_CLOEXEC)
    assert read.call_args_list[0] == call(descriptor, len(raw) + 1)
    if failure is None:
        assert read.call_args_list == [call(descriptor, len(raw) + 1), call(descriptor, 1)]
        assert fstat.call_args_list == [call(descriptor), call(descriptor)]
        assert lstat.call_args_list == [call(path), call(path)]


def test_sole_main_recovery_keeps_eager_observations_legacy_allowance_and_narrow_lineage_catch(monkeypatch):
    paths = [bot.REGULAR_POST_RECEIPT_FILE, bot.MEME_POST_RECEIPT_FILE,
             bot.CONFIRMED_REPLY_RECEIPT_FILE, bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE]
    # These lane receipts share one canonical journal; model unrelated paths explicitly.
    journals = [bot.journal_path_for_receipt(paths[0]),
                *(paths[0].with_name(f"unrelated-journal-{index}") for index in range(3))]
    receipt, events, blocked = {"post_id": 123}, [], []
    record = ["valid", receipt]
    monkeypatch.setattr(bot, "receipt_namespace_entry_exists",
                        lambda path: events.append(("source", path)) or path == paths[0])
    monkeypatch.setattr(bot, "load_regular_post_receipt", lambda: tuple(record))
    monkeypatch.setattr(bot, "load_meme_post_receipt", Mock(side_effect=AssertionError("unselected lane")))
    monkeypatch.setattr(bot, "remote_write_transport_journal_paths", lambda: journals)
    monkeypatch.setattr(bot, "transport_journal_is_blocking",
                        lambda path: events.append(("journal", path)) or path in blocked)
    inspection = Mock(return_value=SimpleNamespace(classification="confirmed_pair"))
    lineage = Mock(return_value=object())
    logger = Mock()
    monkeypatch.setattr(bot, "inspect_transport_state", inspection)
    monkeypatch.setattr(bot, "verify_lane_transport_source_lineage_if_present", lineage)
    monkeypatch.setattr(bot, "log", logger)
    assert bot.confirmed_main_receipt_is_sole_local_recovery_barrier() is True
    assert events == [("source", p) for p in paths] + [("journal", p) for p in journals]
    record[0] = "pending_schedule"
    assert bot.confirmed_main_receipt_is_sole_local_recovery_barrier() is False
    blocked[:] = journals[:2]
    assert bot.confirmed_main_receipt_is_sole_local_recovery_barrier() is False
    assert inspection.call_args_list == [call(journals[0]), call(journals[1])]
    lineage.assert_not_called()
    blocked[:] = journals[:1]
    assert bot.confirmed_main_receipt_is_sole_local_recovery_barrier() is lineage.return_value
    lineage.assert_called_once_with(receipt_path=paths[0], receipt=receipt, lane="quote_image", post_id="123")
    failure = TypeError("lineage")
    lineage.side_effect = failure
    assert bot.confirmed_main_receipt_is_sole_local_recovery_barrier() is False
    logger.critical.assert_called_once()
    inspection.side_effect = failure
    with pytest.raises(TypeError) as caught:
        bot.confirmed_main_receipt_is_sole_local_recovery_barrier()
    assert caught.value is failure


def _open_preflight(monkeypatch):
    values = {
        "confirmed_main_receipt_is_sole_local_recovery_barrier": False,
        "block_if_remote_write_safety_incident_latched": None,
        "historical_context_outbox_remote_attempt_is_blocking": False,
        "block_if_remote_receipt_retirement_exists": None,
        "block_if_remote_write_transport_journal_exists": None,
        "block_if_remote_media_upload_receipt_exists": None,
        "load_regular_post_receipt": ("absent", None), "load_meme_post_receipt": ("absent", None),
        "load_confirmed_reply_receipt": ("absent", None),
        "historical_context_receipt_path_present_or_unsafe": False,
        "historical_context_receipt_parent_for_local_reconciliation": None,
        "exact_historical_context_sending_receipt_matches": False,
        "current_main_post_attempt_is_semantically_valid": True,
    }
    events, probes = [], {}
    for name in values:
        probe = Mock(side_effect=lambda *args, n=name, **kwargs: events.append(n) or values[n])
        probes[name] = probe
        monkeypatch.setattr(bot, name, probe)
    monkeypatch.setattr(
        bot,
        "_reply_receipts_owner",
        Mock(return_value=SimpleNamespace(load=probes["load_confirmed_reply_receipt"])),
    )
    monkeypatch.setattr(
        bot,
        "load_confirmed_reply_receipt",
        Mock(side_effect=AssertionError("barrier returned through root receipt relay")),
    )
    return values, events, probes


@pytest.mark.parametrize("boundary", range(6))
def test_raising_preflight_orders_local_recovery_and_stops_at_each_gate(monkeypatch, boundary):
    values, events, probes = _open_preflight(monkeypatch)
    order = list(values)[:6]
    failure = TypeError("current gate")

    def fail():
        events.append(order[boundary])
        raise failure

    probes[order[boundary]].side_effect = lambda *args, **kwargs: fail()
    with pytest.raises(TypeError) as caught:
        bot.block_if_ambiguous_remote_post(allow_confirmed_pending_schedule_reconciliation=True)
    assert caught.value is failure
    assert events == order[:boundary + 1]


@pytest.mark.parametrize("lane, exception_name", [("regular", "InvalidRegularPostReceipt"), ("meme", "InvalidMemePostReceipt")])
def test_raising_preflight_counts_non_none_receipts_then_eagerly_reads_both_main_lanes(monkeypatch, lane, exception_name):
    values, events, _probes = _open_preflight(monkeypatch)
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="multiple transaction"):
        bot.block_if_ambiguous_remote_post(prepared_main_post_attempt={}, prepared_conversational_reply_receipt={})
    assert events == []
    values[f"load_{lane}_post_receipt"] = ("invalid", None)
    with pytest.raises(getattr(bot, exception_name)):
        bot.block_if_ambiguous_remote_post()
    assert events == list(values)[1:8]


def test_raising_preflight_keeps_narrow_main_and_historical_local_exceptions(monkeypatch):
    values, events, probes = _open_preflight(monkeypatch)
    values["confirmed_main_receipt_is_sole_local_recovery_barrier"] = True
    values["load_regular_post_receipt"] = ("pending_schedule", {"post_id": "123"})
    bot.block_if_ambiguous_remote_post(allow_confirmed_pending_schedule_reconciliation=True)
    assert events == [name for name in list(values)[:10] if name != "block_if_remote_write_transport_journal_exists"]
    probes["block_if_remote_write_transport_journal_exists"].assert_not_called()
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="unresolved main-post"):
        bot.block_if_ambiguous_remote_post()
    values["load_regular_post_receipt"] = ("absent", None)
    values["historical_context_receipt_path_present_or_unsafe"] = True
    values["historical_context_receipt_parent_for_local_reconciliation"] = ""
    bot.block_if_ambiguous_remote_post(allow_historical_context_receipt_reconciliation=True)
    probes["exact_historical_context_sending_receipt_matches"].assert_not_called()
    receipt = unit_historical_context_sending_receipt()
    values["exact_historical_context_sending_receipt_matches"] = True
    bot.block_if_ambiguous_remote_post(prepared_historical_context_reply_receipt=receipt)
    probes["exact_historical_context_sending_receipt_matches"].assert_called_once_with(receipt)
    assert probes["historical_context_outbox_remote_attempt_is_blocking"].call_args.kwargs["prepared_receipt"] is receipt


def test_prepared_global_authority_checks_exact_pair_and_unrelated_journals_before_main_lifecycle(monkeypatch):
    values, events, probes = _open_preflight(monkeypatch)
    receipt = {"lifecycle_state": "attempting"}
    values["load_regular_post_receipt"] = ("sending", receipt)
    journal = bot.journal_path_for_receipt(bot.REGULAR_POST_RECEIPT_FILE)
    authority = SimpleNamespace(journal_path=str(journal), lifecycle_state="prepared",
                                transaction_id="transaction", journal_sha256="journal", fence_sha256="fence")
    state = SimpleNamespace(classification="prepared_pair",
                            journal=SimpleNamespace(document={"transaction_id": "transaction"}, sha256="journal"),
                            fence=SimpleNamespace(sha256="fence"))
    unrelated = [journal.with_name("other-one"), journal.with_name("other-two")]
    inspection, blocking = Mock(return_value=state), Mock(return_value=False)
    monkeypatch.setattr(bot, "inspect_transport_state", inspection)
    monkeypatch.setattr(bot, "remote_write_transport_journal_paths", lambda: [journal, *unrelated])
    monkeypatch.setattr(bot, "transport_journal_is_blocking", blocking)
    bot.block_if_ambiguous_remote_post(prepared_main_post_attempt=receipt, prepared_transport_authority=authority)
    inspection.assert_called_once_with(journal)
    assert blocking.call_args_list == [call(p) for p in unrelated]
    probes["current_main_post_attempt_is_semantically_valid"].assert_called_once_with(receipt)
    events.clear()
    blocking.reset_mock()
    state.fence.sha256 = "stale"
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="sole exact transport barrier"):
        bot.block_if_ambiguous_remote_post(prepared_main_post_attempt=receipt, prepared_transport_authority=authority)
    assert blocking.call_args_list == [call(p) for p in unrelated]
    assert events == list(values)[1:4]
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="unresolved main-post"):
        bot.block_if_ambiguous_remote_post(prepared_main_post_attempt=receipt)
    receipt["lifecycle_state"] = "sending"
    bot.block_if_ambiguous_remote_post(prepared_main_post_attempt=receipt)

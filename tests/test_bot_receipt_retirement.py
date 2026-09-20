"""Focused contracts for receipt retirement and exact source verification."""
from __future__ import annotations

import inspect
import functools
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import historical_context_formatter as formatter
import mrsMThatcher2 as bot
import mrs_bot_receipt_retirement as owner
from tests.helpers.receipt_fixtures import production_lane_documents
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401

DEPENDENCIES = {'confirmed_context_outbox_matches_receipt': [],
 'require_historical_context_retirement_outbox_authority': ['ExactReceiptRetirementError',
                                                            'HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE',
                                                            'historical_context_outbox_store',
                                                            'historical_context_reply_store',
                                                            'inspect_exact_receipt_retirement',
                                                            'inspect_interrupted_receipt_retirement',
                                                            'journal_path_for_receipt',
                                                            'now_epoch',
                                                            're',
                                                            'retirement_auxiliary_barrier_exists',
                                                            'transport_journal_is_blocking'],
 'resume_interrupted_source_receipt_retirement_if_present': ['CONFIRMED_REPLY_RECEIPT_FILE',
                                                             'ExactReceiptRetirementError',
                                                             'HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE',
                                                             'MEME_POST_RECEIPT_FILE',
                                                             'REGULAR_POST_RECEIPT_FILE',
                                                             'historical_context_reply_store',
                                                             'inspect_transport_state',
                                                             'inspect_interrupted_receipt_retirement',
                                                             'journal_path_for_receipt',
                                                             'load_confirmed_reply_receipt',
                                                             'load_meme_post_receipt',
                                                             'load_regular_post_receipt',
                                                             'log',
                                                             'remote_write_transport_journal_paths',
                                                             'require_historical_context_retirement_outbox_authority',
                                                             'resume_interrupted_receipt_retirement',
                                                             'retire_lane_transport_journal_if_present',
                                                             'retirement_auxiliary_barrier_exists',
                                                             'transaction_mutation_authority',
                                                             'recover_state_receipt_commit_proof',
                                                             'state_commit_mutation_authority',
                                                             'transport_journal_is_blocking'],
 'retire_current_source_receipt': ['latch_source_receipt_retirement_uncertainty',
                                   'retire_or_resume_exact_receipt',
                                   'transaction_mutation_authority'],
 'resume_interrupted_confirmed_media_retirement_if_present': ['MEDIA_UPLOAD_RECEIPT_FILE',
                                                              'MEME_POST_RECEIPT_FILE',
                                                              'MediaUploadReceiptError',
                                                              'Path',
                                                              'REGULAR_POST_RECEIPT_FILE',
                                                              'fence_path_for_journal',
                                                              'inspect_transport_state',
                                                              'journal_path_for_receipt',
                                                              'log',
                                                              'media_fence_path_for_receipt',
                                                              'os',
                                                              'require_instance_lock_for_remote_write',
                                                              'resume_interrupted_confirmed_media_retirement',
                                                              'transaction_mutation_authority'],
 'expected_lane_transport_source_receipt_bytes': ['TransportJournalError',
                                                  'confirmed_pending_schedule_receipt_is_semantically_valid',
                                                  'conversational_sending_receipt_from_confirmed',
                                                  'current_main_post_attempt_is_semantically_valid',
                                                  'hashlib',
                                                  're',
                                                  'sending_reply_receipt_is_semantically_valid'],
 'verify_lane_transport_source_lineage_if_present': ['TRANSPORT_SOURCE_VALIDATOR_ID',
                                                     'TransportJournalError',
                                                     'expected_lane_transport_source_receipt_bytes',
                                                     'journal_path_for_receipt',
                                                     'receipt_int',
                                                     'transport_journal_is_blocking',
                                                     'verify_confirmed_transport_source_lineage'],
 'retire_lane_transport_journal_if_present': [
                                              'expected_lane_transport_source_receipt_bytes',
                                              'journal_path_for_receipt',
                                              'prepare_exact_receipt_retirement',
                                              'retire_confirmed_transport_transaction',
                                              'transaction_mutation_authority',
                                              'transport_journal_is_blocking']}

SIGNATURES = {'confirmed_context_outbox_matches_receipt': "(context_reply: 'dict', receipt: 'dict') -> 'bool'",
 'require_historical_context_retirement_outbox_authority': "() -> 'None'",
 'resume_interrupted_source_receipt_retirement_if_present': "() -> 'bool'",
 'retire_current_source_receipt': "(receipt_path: 'Path', expected_receipt_bytes: 'bytes', *, commit_proof=None, disposition: 'str | None' = None) -> "
                                  "'None'",
 'resume_interrupted_confirmed_media_retirement_if_present': "() -> 'bool'",
 'expected_lane_transport_source_receipt_bytes': "(*, receipt: 'dict', lane: 'str', "
                                                 "current_receipt_bytes: 'bytes') -> 'bytes'",
 'verify_lane_transport_source_lineage_if_present': "(*, receipt_path: 'Path', receipt: 'dict', "
                                                    "lane: 'str', post_id: 'str', "
                                                    "current_receipt_bytes: 'bytes | None' = None) "
                                                    "-> 'bool'",
 'retire_lane_transport_journal_if_present': "(*, receipt_path: 'Path', receipt: 'dict', lane: "
                                             "'str', post_id: 'str', current_receipt_bytes: 'bytes "
                                             "| None' = None, commit_proof=None) -> 'bool'"}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, json, io, logging, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Receipt retirement import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_receipt_retirement', 'mrs_bot_durable_json_io'}:
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
import mrs_bot_receipt_retirement
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
                patch.setattr(bot, dep, value)
            result = {"original": []}
            expected = {}

            def capture(*args, **kwargs):
                assert len(args) == len(positional)
                assert all(value is expected[key] for key, value in zip(positional, args))
                supplied = {key: expected[key] for key in keyword_only} | current
                if name in {"retire_current_source_receipt", "retire_lane_transport_journal_if_present"}:
                    proof = supplied.pop("commit_proof")
                    authority = kwargs["transaction_mutation_authority"]
                    assert isinstance(authority, functools.partial)
                    assert authority.func is bot.state_commit_mutation_authority
                    assert authority.args == (proof,)
                    assert authority.keywords == {}
                    supplied["transaction_mutation_authority"] = authority
                assert kwargs.keys() == supplied.keys()
                assert all(kwargs[key] is value for key, value in supplied.items())
                return result

            patch.setattr(bot, "_receipt_retirement", SimpleNamespace(**{name: capture}))
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
            patch.setattr(bot, "_receipt_retirement", SimpleNamespace(**{name: Mock(side_effect=failure)}))
            with pytest.raises(TypeError) as caught:
                adapter(**provided)
            assert caught.value is failure


@pytest.mark.parametrize("shape, expected", [
    ("current", True), ("wrong_source_attempt", False),
    ("legacy", True), ("legacy_ordinal", False),
    ("partial_legacy", True), ("missing_outbox_source", False),
    ("present_none", True), ("wrong_state", False),
])
def test_matcher_is_exact_alias_with_distinct_legacy_and_source_ordinals(shape, expected):
    matcher = bot.confirmed_context_outbox_matches_receipt
    assert matcher is owner.confirmed_context_outbox_matches_receipt
    assert str(inspect.signature(matcher)) == SIGNATURES[matcher.__name__]
    context = dict(state="context_reply_confirmed", quote_id="q", reply_post_id="p",
                   attempt_count=7, source_receipt_sha256="hash",
                   source_receipt_attempt_number=19)
    receipt = dict(quote_id="q", reply_post_id="p", attempt_number=19,
                   source_receipt_sha256="hash")
    if shape in {"legacy", "legacy_ordinal", "partial_legacy"}:
        receipt.pop("source_receipt_sha256")
        context.pop("source_receipt_attempt_number")
        if shape != "partial_legacy":
            context.pop("source_receipt_sha256")
        if shape != "legacy_ordinal":
            receipt.pop("attempt_number")
    elif shape == "wrong_source_attempt":
        context["source_receipt_attempt_number"] = 7
    elif shape == "missing_outbox_source":
        context.pop("source_receipt_sha256")
    elif shape == "present_none":
        context["source_receipt_sha256"] = receipt["source_receipt_sha256"] = None
    elif shape == "wrong_state":
        context["state"] = "context_reply_attempting"
    assert matcher(context, receipt) is expected
    assert matcher([], receipt) is False


def test_early_gates_skip_historical_imports_serializers_and_authority(monkeypatch):
    monkeypatch.setitem(sys.modules, "historical_context_formatter", None)
    monkeypatch.setattr(bot, "retirement_auxiliary_barrier_exists", lambda path: False)
    monkeypatch.setattr(bot, "transport_journal_is_blocking", lambda path: False)
    for name in ("historical_context_reply_store", "historical_context_outbox_store",
                 "remote_write_transport_journal_paths", "canonical_atomic_json_bytes",
                 "expected_lane_transport_source_receipt_bytes", "transaction_mutation_authority"):
        target = owner if name == "canonical_atomic_json_bytes" else bot
        monkeypatch.setattr(target, name, Mock(side_effect=AssertionError(name)))
    assert bot.require_historical_context_retirement_outbox_authority() is None
    assert bot.resume_interrupted_source_receipt_retirement_if_present() is False
    options = dict(receipt_path=bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
                   receipt=object(), lane="historical_context_reply", post_id=object())
    assert bot.verify_lane_transport_source_lineage_if_present(**options) is False
    assert bot.retire_lane_transport_journal_if_present(**options) is False


@pytest.mark.parametrize("boundary", ["confirm", "marker", "duplicate", "ordinal", "terminal"])
def test_context_authority_keeps_call_time_formatter_unique_history_and_confirmation_order(
    monkeypatch, boundary,
):
    _, receipt, _, receipt_bytes, post_id = production_lane_documents("historical_context_reply")
    path = bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
    events = []
    context = dict(state="context_reply_attempting", quote_id=receipt["quote_id"],
                   attempt_count="7", remote_transaction_started=True,
                   source_receipt_sha256=receipt["source_receipt_sha256"],
                   source_receipt_attempt_number=receipt["attempt_number"])
    if boundary == "ordinal":
        context["attempt_count"] = "not-an-integer"
    if boundary == "terminal":
        context["state"] = "context_reply_confirmed"
    row = {**receipt, "status": "completed"}
    items = {"first": row, "ignored": None}
    if boundary == "duplicate":
        items["second"] = row

    def note(label, result):
        events.append(label)
        return result

    def canonical(value):
        assert value == receipt and "status" not in value
        return note("canonical", receipt_bytes)

    def inspect_exact(actual_path, data):
        assert actual_path is path and data is receipt_bytes
        return note("exact", SimpleNamespace(valid=True))

    def matcher(actual_context, actual_receipt):
        assert actual_context is context and actual_receipt == receipt
        return note("matcher", True)

    clock_value = object()
    confirmed = Mock(side_effect=lambda *args, **kwargs: note("confirmed", None))
    outbox = SimpleNamespace(
        get=Mock(side_effect=lambda parent: note("get", {"context_reply": context})),
        record_confirmed=confirmed,
    )
    monkeypatch.setitem(sys.modules, "historical_context_formatter", SimpleNamespace(
        HistoricalContextReplyStore=object, canonical_json_bytes=canonical,
    ))
    monkeypatch.setattr(bot, "retirement_auxiliary_barrier_exists", lambda p: note("gate", True))
    monkeypatch.setattr(bot, "historical_context_reply_store", lambda: note("store", SimpleNamespace(
        history=lambda: note("history", {"items": items}),
    )))
    monkeypatch.setattr(bot, "inspect_interrupted_receipt_retirement", lambda p: note(
        "marker", SimpleNamespace(valid=boundary != "marker", expected_sha256="a" * 64),
    ))
    monkeypatch.setattr(bot, "inspect_exact_receipt_retirement", inspect_exact)
    monkeypatch.setattr(bot, "historical_context_outbox_store", lambda: note("outbox", outbox))
    monkeypatch.setattr(owner, "confirmed_context_outbox_matches_receipt", matcher)
    monkeypatch.setattr(bot, "now_epoch", lambda: note("clock", clock_value))
    monkeypatch.setattr(bot, "transport_journal_is_blocking", Mock(side_effect=AssertionError("journal")))
    if boundary in {"marker", "duplicate", "ordinal"}:
        error = ValueError if boundary == "ordinal" else bot.ExactReceiptRetirementError
        with pytest.raises(error):
            bot.require_historical_context_retirement_outbox_authority()
        confirmed.assert_not_called()
        assert "clock" not in events
    else:
        assert bot.require_historical_context_retirement_outbox_authority() is None
    prefix = ["gate", "store", "marker"]
    if boundary != "marker":
        prefix += ["history", "canonical", "exact"]
        if boundary == "duplicate":
            prefix += ["canonical", "exact"]
        else:
            prefix += ["outbox", "get"]
            outbox.get.assert_called_once_with(str(receipt["parent_post_id"]))
    suffix = {"confirm": ["clock", "confirmed"], "terminal": ["matcher"]}.get(boundary, [])
    assert events == prefix + suffix
    if boundary == "confirm":
        confirmed.assert_called_once_with(receipt["parent_post_id"], attempt_number=7,
                                          reply_post_id=post_id, confirmed_epoch=clock_value)


@pytest.mark.parametrize("stop", [None, "context", "retire_journal", "authority", "resume"])
def test_source_resume_orders_context_journal_and_current_source_authority(monkeypatch, tmp_path, stop):
    _, receipt, _, current_bytes, post_id = production_lane_documents("historical_context_reply")
    path = bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
    journal_path = tmp_path / "owning-journal"
    token, phase = object(), object()
    events, scanned = [], []
    failure = TypeError("source retirement boundary")

    def note(label, result=None):
        events.append(label)
        if stop == label:
            raise failure
        return result

    def active(candidate):
        scanned.append(candidate)
        return candidate is path

    def retire_journal(**kwargs):
        assert kwargs == dict(receipt_path=path, receipt=receipt, lane="historical_context_reply",
                              post_id=post_id, current_receipt_bytes=current_bytes, commit_proof=None)
        assert kwargs["receipt"] is receipt and kwargs["current_receipt_bytes"] is current_bytes
        return note("retire_journal", True)

    monkeypatch.setattr(bot, "retirement_auxiliary_barrier_exists", active)
    monkeypatch.setattr(bot, "require_historical_context_retirement_outbox_authority", lambda: note("context"))
    monkeypatch.setattr(bot, "remote_write_transport_journal_paths", lambda: note("journals", [journal_path]))
    monkeypatch.setattr(bot, "transport_journal_is_blocking", lambda p: True)
    monkeypatch.setattr(bot, "journal_path_for_receipt", lambda p: journal_path)
    monkeypatch.setattr(bot, "inspect_transport_state", lambda p: note("inspect", SimpleNamespace(
        journal=SimpleNamespace(document={"lane": "historical_context_reply", "remote_post_id": post_id}),
    )))
    monkeypatch.setattr(bot, "historical_context_reply_store", lambda: note("store", SimpleNamespace(
        _load_receipt_safely=lambda: note("load", (receipt, current_bytes)),
    )))
    monkeypatch.setattr(bot, "retire_lane_transport_journal_if_present", retire_journal)
    authority = Mock(side_effect=lambda operation: note("authority", token))
    resume = Mock(side_effect=lambda *args, **kwargs: note("resume", SimpleNamespace(initial_phase=phase)))
    logger = Mock()
    monkeypatch.setattr(bot, "transaction_mutation_authority", authority)
    monkeypatch.setattr(bot, "resume_interrupted_receipt_retirement", resume)
    monkeypatch.setattr(bot, "log", logger)
    order = ["context", "journals", "inspect", "store", "load", "retire_journal", "authority", "resume"]
    if stop:
        with pytest.raises(TypeError) as caught:
            bot.resume_interrupted_source_receipt_retirement_if_present()
        assert caught.value is failure
        assert events == order[:order.index(stop) + 1]
        logger.warning.assert_not_called()
    else:
        assert bot.resume_interrupted_source_receipt_retirement_if_present() is True
        assert events == order
        authority.assert_called_once_with("interrupted source receipt retirement resume")
        resume.assert_called_once_with(path, mutation_authority=token)
        assert logger.warning.call_args.args[1:] == (path, phase)
    assert scanned == [bot.REGULAR_POST_RECEIPT_FILE, bot.MEME_POST_RECEIPT_FILE,
                       bot.CONFIRMED_REPLY_RECEIPT_FILE, path]


@pytest.mark.parametrize("boundary", ["lock", "receipt", "absent", "os_error", "native"])
def test_media_resume_keeps_lock_first_and_narrow_lstat_errors(monkeypatch, tmp_path, boundary):
    path = tmp_path / "media.json"
    fence = tmp_path / "media.fence"
    events = []
    failure = ValueError("native namespace error") if boundary == "native" else OSError("namespace error")

    def lock(operation):
        events.append("lock")
        assert operation == "Interrupted confirmed-media retirement recovery"
        if boundary == "lock":
            raise failure

    def lstat(candidate):
        events.append(candidate)
        if boundary in {"os_error", "native"}:
            raise failure
        if boundary == "receipt":
            return object()
        raise FileNotFoundError(candidate)

    monkeypatch.setattr(bot, "MEDIA_UPLOAD_RECEIPT_FILE", path)
    monkeypatch.setattr(bot, "media_fence_path_for_receipt", lambda p: fence)
    monkeypatch.setattr(bot, "require_instance_lock_for_remote_write", lock)
    monkeypatch.setattr(bot, "os", SimpleNamespace(lstat=lstat))
    monkeypatch.setattr(bot, "inspect_transport_state", Mock(side_effect=AssertionError("owner")))
    if boundary in {"lock", "os_error", "native"}:
        expected = bot.MediaUploadReceiptError if boundary == "os_error" else type(failure)
        with pytest.raises(expected) as caught:
            bot.resume_interrupted_confirmed_media_retirement_if_present()
        assert (caught.value.__cause__ if boundary == "os_error" else caught.value) is failure
    else:
        assert bot.resume_interrupted_confirmed_media_retirement_if_present() is False
    assert events == ["lock"] + ([] if boundary == "lock" else [path]) + ([fence] if boundary == "absent" else [])


@pytest.mark.parametrize("boundary", ["success", "owner", "lane", "result"])
def test_media_resume_preserves_prepared_owner_and_exact_retired_result(monkeypatch, tmp_path, boundary):
    media, fence, journal = (tmp_path / name for name in ("media", "media-fence", "journal"))
    source = bot.MEME_POST_RECEIPT_FILE
    events, token = [], object()
    result = SimpleNamespace(state="retired", lane="daily_meme", media_transaction_id="tx", media_id="id")
    document = {"lane": "unsupported" if boundary == "lane" else "daily_meme",
                "source_receipt": {"basename": source.name}}
    state = SimpleNamespace(blocking=True, classification="bad" if boundary == "owner" else "prepared_pair",
                            journal=SimpleNamespace(document=document), fence=object())

    def lstat(path):
        if path == media:
            raise FileNotFoundError(path)
        assert path == fence
        return object()

    monkeypatch.setattr(bot, "MEDIA_UPLOAD_RECEIPT_FILE", media)
    monkeypatch.setattr(bot, "require_instance_lock_for_remote_write", lambda op: events.append("lock"))
    monkeypatch.setattr(bot, "os", SimpleNamespace(lstat=lstat))
    monkeypatch.setattr(bot, "media_fence_path_for_receipt", lambda p: fence)
    monkeypatch.setattr(bot, "journal_path_for_receipt", lambda p: journal)
    monkeypatch.setattr(bot, "inspect_transport_state", lambda p: events.append("owner") or state)
    authority = Mock(side_effect=lambda op: events.append("authority") or token)
    resume = Mock(side_effect=lambda *args, **kwargs: events.append("resume") or (None if boundary == "result" else result))
    logger = Mock()
    monkeypatch.setattr(bot, "transaction_mutation_authority", authority)
    monkeypatch.setattr(bot, "resume_interrupted_confirmed_media_retirement", resume)
    monkeypatch.setattr(bot, "log", logger)
    if boundary != "success":
        with pytest.raises(bot.MediaUploadReceiptError):
            bot.resume_interrupted_confirmed_media_retirement_if_present()
        logger.warning.assert_not_called()
    else:
        assert bot.resume_interrupted_confirmed_media_retirement_if_present() is True
        assert logger.warning.call_args.args[1:] == (result.lane, result.media_transaction_id, result.media_id)
    assert state.journal.document is document and state.classification != "retired"
    assert events == ["lock", "owner"] + (["authority", "resume"] if boundary in {"success", "result"} else [])
    if boundary in {"success", "result"}:
        authority.assert_called_once_with("interrupted media retirement resume")
        resume.assert_called_once_with(media, mutation_authority=token, transport_journal_path=journal,
                                       transport_fence_path=bot.fence_path_for_journal(journal), source_receipt_path=source)


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme", "conversational_reply"])
def test_source_reconstruction_preserves_current_validator_and_byte_references(monkeypatch, lane):
    source, _, source_bytes, _, _ = production_lane_documents(lane)
    current_bytes = b"exact caller-owned bytes without normalization"
    validator = Mock(return_value=True)
    name = "sending_reply_receipt_is_semantically_valid" if lane == "conversational_reply" else "current_main_post_attempt_is_semantically_valid"
    monkeypatch.setattr(bot, name, validator)
    serializer = Mock(side_effect=AssertionError("unexpected serialization"))
    monkeypatch.setattr(owner, "canonical_atomic_json_bytes", serializer)
    assert bot.expected_lane_transport_source_receipt_bytes(
        receipt=source, lane=lane, current_receipt_bytes=current_bytes,
    ) is current_bytes
    validator.assert_called_once_with(source)
    if lane != "conversational_reply":
        validator.return_value = False
        pending = {"receipt_type": "confirmed_pending_schedule", "source_attempt": source}
        pending_validator = Mock(return_value=True)
        monkeypatch.setattr(bot, "confirmed_pending_schedule_receipt_is_semantically_valid", pending_validator)
        serializer.side_effect, serializer.return_value = None, source_bytes
        assert bot.expected_lane_transport_source_receipt_bytes(
            receipt=pending, lane=lane, current_receipt_bytes=current_bytes,
        ) is source_bytes
        pending_validator.assert_called_once_with(pending, expected_lane=lane)
        assert serializer.call_args.args[0] is source


@pytest.mark.parametrize("error_type", [TypeError, ValueError, KeyError])
def test_source_reconstruction_keeps_byte_gate_and_narrow_call_time_historical_cause(monkeypatch, error_type):
    receipt, current_bytes = object(), b"current"
    failure = error_type("historical source failure")
    valid = Mock(return_value=False)
    reconstruct = Mock(side_effect=failure)
    monkeypatch.setitem(sys.modules, "historical_context_formatter", SimpleNamespace(
        HistoricalContextReplyStore=SimpleNamespace(_valid_sending_receipt=valid,
                                                   source_receipt_bytes_from_confirmed=reconstruct),
    ))
    for data in (None, b"", bytearray(b"current")):
        with pytest.raises(bot.TransportJournalError, match="current lane receipt bytes are invalid"):
            bot.expected_lane_transport_source_receipt_bytes(receipt=receipt, lane="historical_context_reply", current_receipt_bytes=data)
    valid.assert_not_called()
    expected = bot.TransportJournalError if error_type in {TypeError, ValueError} else error_type
    with pytest.raises(expected) as caught:
        bot.expected_lane_transport_source_receipt_bytes(receipt=receipt, lane="historical_context_reply", current_receipt_bytes=current_bytes)
    assert (caught.value.__cause__ if expected is bot.TransportJournalError else caught.value) is failure
    assert reconstruct.call_args.args[0] is receipt
    valid.return_value = True
    assert bot.expected_lane_transport_source_receipt_bytes(receipt=receipt, lane="historical_context_reply", current_receipt_bytes=current_bytes) is current_bytes
    reconstruct.assert_called_once_with(receipt)


@pytest.mark.parametrize("lane, pending, field, supplied", [
    ("quote_image", False, "quote_post_epoch", True),
    ("daily_meme", False, "meme_post_epoch", True),
    ("quote_image", True, "confirmation_epoch", False),
    ("daily_meme", True, "confirmation_epoch", False),
    ("conversational_reply", False, "confirmation_epoch", True),
    ("historical_context_reply", False, None, False),
])
def test_lineage_verification_keeps_byte_references_and_lane_confirmation_epochs(monkeypatch, tmp_path, lane, pending, field, supplied):
    receipt = dict(quote_post_epoch="11", meme_post_epoch="22", confirmation_epoch="33")
    if pending:
        receipt["receipt_type"] = "confirmed_pending_schedule"
    path, journal, post_id = tmp_path / "receipt", tmp_path / "journal", object()
    current_bytes, source_bytes, validator_id = b"current bytes", b"source bytes", object()
    events = []
    details = SimpleNamespace(confirmation_epoch=123)
    serializer = Mock(side_effect=lambda value: events.append("serialize") or current_bytes)
    expected = Mock(side_effect=lambda **kwargs: events.append("source") or source_bytes)
    verify = Mock(side_effect=lambda **kwargs: events.append("verify") or details)
    integer = Mock(side_effect=lambda value: events.append("epoch") or 123)
    monkeypatch.setattr(bot, "journal_path_for_receipt", lambda value: journal)
    monkeypatch.setattr(bot, "transport_journal_is_blocking", lambda value: True)
    monkeypatch.setattr(owner, "canonical_atomic_json_bytes", serializer)
    monkeypatch.setitem(sys.modules, "historical_context_formatter", SimpleNamespace(canonical_json_bytes=serializer))
    monkeypatch.setattr(bot, "expected_lane_transport_source_receipt_bytes", expected)
    monkeypatch.setattr(bot, "verify_confirmed_transport_source_lineage", verify)
    monkeypatch.setattr(bot, "TRANSPORT_SOURCE_VALIDATOR_ID", validator_id)
    monkeypatch.setattr(bot, "receipt_int", integer)
    options = dict(receipt_path=path, receipt=receipt, lane=lane, post_id=post_id,
                   current_receipt_bytes=current_bytes if supplied else None)
    assert bot.verify_lane_transport_source_lineage_if_present(**options) is True
    assert events == ([] if supplied else ["serialize"]) + ["source", "verify"] + (["epoch"] if field else [])
    assert expected.call_args.kwargs["receipt"] is receipt
    assert expected.call_args.kwargs["current_receipt_bytes"] is current_bytes
    verify.assert_called_once_with(receipt_path=path, expected_source_receipt_bytes=source_bytes,
                                   lane=lane, post_id=post_id, validator_id=validator_id)
    if field:
        integer.assert_called_once_with(receipt[field])
    else:
        integer.assert_not_called()
    details.confirmation_epoch = 999
    if field:
        with pytest.raises(bot.TransportJournalError, match="time differs"):
            bot.verify_lane_transport_source_lineage_if_present(**options)
    else:
        assert bot.verify_lane_transport_source_lineage_if_present(**options) is True


@pytest.mark.parametrize("stop", [None, "source", "prepare", "authority2", "retire"])
def test_journal_retirement_orders_source_guard_and_separately_issued_authorities(monkeypatch, tmp_path, stop):
    path, journal, receipt, post_id = tmp_path / "receipt", tmp_path / "journal", {"shared": []}, object()
    current_bytes, source_bytes = b"current exact bytes", b"source exact bytes"
    tokens, events = [object(), object()], []
    failure = OSError("retirement boundary")

    def note(label, result=None):
        events.append(label)
        if label == stop:
            raise failure
        return result

    authority = Mock(side_effect=lambda op: note("authority1" if op == "source receipt retirement preparation" else "authority2",
                                                tokens[0] if op == "source receipt retirement preparation" else tokens[1]))
    expected = Mock(side_effect=lambda **kwargs: note("source", source_bytes))
    prepare = Mock(side_effect=lambda *args, **kwargs: note("prepare"))
    retire = Mock(side_effect=lambda **kwargs: note("retire"))
    monkeypatch.setattr(bot, "journal_path_for_receipt", lambda value: journal)
    monkeypatch.setattr(bot, "transport_journal_is_blocking", lambda value: True)
    monkeypatch.setattr(owner, "canonical_atomic_json_bytes", Mock(side_effect=AssertionError("serialization")))
    monkeypatch.setattr(bot, "expected_lane_transport_source_receipt_bytes", expected)
    monkeypatch.setattr(bot, "transaction_mutation_authority", authority)
    monkeypatch.setattr(bot, "prepare_exact_receipt_retirement", prepare)
    monkeypatch.setattr(bot, "retire_confirmed_transport_transaction", retire)
    options = dict(receipt_path=path, receipt=receipt, lane="quote_image", post_id=post_id, current_receipt_bytes=current_bytes)
    def invoke_owner():
        return owner.retire_lane_transport_journal_if_present(
            **options, **{name: getattr(bot, name) for name in
                         DEPENDENCIES["retire_lane_transport_journal_if_present"]},
        )

    order = ["source", "authority1", "prepare", "authority2", "retire"]
    if stop:
        with pytest.raises(OSError) as caught:
            invoke_owner()
        assert caught.value is failure
        assert events == order[:order.index(stop) + 1]
    else:
        assert invoke_owner() is True
        assert events == order
        assert authority.call_args_list == [call("source receipt retirement preparation"), call("confirmed transport journal retirement")]
        assert prepare.call_args.args[0] is path and prepare.call_args.args[1] is current_bytes
        prepare.assert_called_once_with(path, current_bytes, mutation_authority=tokens[0])
        retire.assert_called_once_with(mutation_authority=tokens[1], receipt_path=path,
                                       expected_confirmed_receipt=receipt, expected_source_receipt_bytes=source_bytes,
                                       expected_current_receipt_bytes=current_bytes, source_retirement_prepared=True,
                                       lane="quote_image", post_id=post_id)
        assert retire.call_args.kwargs["expected_confirmed_receipt"] is receipt
        assert retire.call_args.kwargs["expected_source_receipt_bytes"] is source_bytes
    assert expected.call_args.kwargs["receipt"] is receipt
    assert expected.call_args.kwargs["current_receipt_bytes"] is current_bytes


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme", "conversational_reply"])
@pytest.mark.parametrize("proof", [None, object()])
def test_root_retirement_requires_real_state_proof_before_owner(monkeypatch, tmp_path, lane, proof):
    invoked = Mock(side_effect=AssertionError("retirement owner must not run"))
    monkeypatch.setattr(bot, "_receipt_retirement", SimpleNamespace(
        retire_lane_transport_journal_if_present=invoked,
    ))
    with pytest.raises(RuntimeError, match="exact durable state commit"):
        bot.retire_lane_transport_journal_if_present(
            receipt_path=tmp_path / "receipt", receipt={}, lane=lane,
            post_id="950001", commit_proof=proof,
        )
    invoked.assert_not_called()

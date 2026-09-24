"""Focused contracts for receipt-bound media and public-post creation adapters."""
from __future__ import annotations

import copy
import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_post_creation as owner
from mrs_bot_main_post_assembly import MainPostAssembly
from mrs_bot_main_post_receipt_storage import MainPostReceipts
from mrs_bot_main_post_receipts import MainPostReceiptValues
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


DEPENDENCIES = {'validate_media_upload_payload_metadata': [],
 'media_upload_payload_metadata': [],
 'upload_media_v2': ['AmbiguousRemotePostOutcome',
                     'log',
                     'x_request'],
 'upload_media': ['AmbiguousRemotePostOutcome',
                  'MEDIA_UPLOAD_RECEIPT_FILE',
                  'MediaUploadPreflightError',
                  'MediaUploadReceiptError',
                  'RemoteOperationsPaused',
                  'abort_untransmitted_media_upload',
                  'begin_confirmed_post_sigint_deferral',
                  'begin_media_upload',
                  'bind_media_upload_payload',
                  'block_if_ambiguous_remote_post',
                  'confirm_media_upload',
                  'end_confirmed_post_sigint_deferral',
                  'log',
                  'mimetypes',
                  'record_ambiguous_remote_post',
                  'require_remote_operation_unpaused',
                  'transaction_mutation_authority',
                  'upload_media_v2'],
 'create_post': ['AmbiguousRemotePostOutcome',
                 'CONFIRMED_REPLY_RECEIPT_FILE',
                 'HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE',
                 'ProvedRemotePostNonSuccess',
                 'RemoteOperationsPaused',
                 'TransportJournalError',
                 'abort_untransmitted_transport_transaction',
                 'arm_transport_transaction',
                 'begin_transport_transaction',
                 'bind_lane_transport_source',
                 'block_if_ambiguous_remote_post',
                 'block_if_remote_write_safety_incident_latched',
                 'confirm_transport_transaction',
                 'confirmation_epoch_after_remote_success',
                 'freeze_tweet_request',
                 'global_remote_writes_paused',
                 'journal_path_for_receipt',
                 'log',
                 'receipts',
                 'receipt_values',
                 'record_ambiguous_remote_post',
                 'require_instance_lock_for_remote_write',
                 'retire_consumed_transport_transaction_after_proved_remote_non_success',
                 'sending_reply_receipt_is_semantically_valid',
                 'transaction_mutation_authority',
                 'x_request'],
 'handoff_confirmed_media_upload_to_main_attempt': ['MEDIA_UPLOAD_RECEIPT_FILE',
                                                    'MediaUploadReceiptError',
                                                    'bind_media_handoff_to_transport',
                                                    'validate_confirmed_media_upload_metadata',
                                                    'load_confirmed_media_upload',
                                                    'log',
                                                    'receipts',
                                                    'retire_confirmed_media_upload',
                                                    'transaction_mutation_authority']}

SIGNATURES = {'validate_media_upload_payload_metadata': "(value: 'object', *, form: 'dict[str, "
                                           "object]') -> 'dict[str, object]'",
 'media_upload_payload_metadata': "(form: 'dict[str, object]') -> 'dict[str, object]'",
 'upload_media_v2': "(*, authority: 'MediaUploadAuthority', payload: "
                    "'ReceiptBoundMediaPayload', payload_metadata: 'dict[str, object] | "
                    "None' = None) -> 'str'",
 'upload_media': "(image_path: 'str', *, lane: 'str') -> 'str'",
 'create_post': "(text: 'str', media_ids: 'list[str] | None' = None, reply_to_id: 'str | "
                "None' = None, made_with_ai: 'bool' = False, *, "
                "prepared_conversational_reply_receipt: 'dict | None' = None, "
                "prepared_historical_context_reply_receipt: 'dict | None' = None, "
                "prepared_main_post_attempt: 'dict | None' = None, "
                "prepared_transport_authority: 'TransportAuthority | None' = None, "
                "prepared_transport_source: 'SourceReceiptBinding | None' = None, "
                "on_remote_transaction_started: 'Callable[[], None] | None' = None) -> "
                "'dict'",
 'handoff_confirmed_media_upload_to_main_attempt': "(attempt: 'dict', "
                                                   'transport_authority: '
                                                   "'TransportAuthority') -> 'None'"}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, dataclasses, io, logging, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Post creation import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_post_creation', 'mrs_bot_receipt_primitives'}:
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
import mrs_bot_post_creation
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
    if name == "create_post":
        assert {"reply_receipt_validator", "reply_receipts"} <= signature.parameters.keys()
    else:
        assert str(signature) == SIGNATURES[name]
    positional = [p.name for p in signature.parameters.values()
                  if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD]
    keyword_only = [p.name for p in signature.parameters.values()
                    if p.kind is inspect.Parameter.KEYWORD_ONLY]
    for _ in range(2):
        with monkeypatch.context() as patch:
            current = {dep: object() for dep in DEPENDENCIES[name]}
            factories = {}
            for dep, value in current.items():
                if dep == "receipts":
                    factories[dep] = Mock(return_value=value)
                    patch.setattr(MainPostAssembly, "receipts", lambda _assembly, _factory=factories[dep], **_options: _factory(**_options))
                elif dep == "receipt_values":
                    factories[dep] = Mock(return_value=value)
                    patch.setattr(MainPostAssembly, "values", lambda _assembly, _factory=factories[dep]: _factory())
                else:
                    patch.setattr(bot, dep, value)
            result = {"original": []}
            expected = {}

            def capture(*args, **kwargs):
                assert len(args) == len(positional)
                assert all(value is expected[key] for key, value in zip(positional, args))
                supplied = {key: expected[key] for key in keyword_only
                            if key not in {"reply_receipt_validator", "reply_receipts"}} | current
                assert kwargs.keys() == supplied.keys()
                for key, value in supplied.items():
                    if (
                        name == "create_post"
                        and key == "block_if_ambiguous_remote_post"
                        and expected.get("prepared_conversational_reply_receipt") is not None
                    ):
                        assert callable(kwargs[key])
                    else:
                        assert kwargs[key] is value
                return result

            patch.setattr(bot, "_post_creation", SimpleNamespace(**{name: capture}))
            for include_defaults in (True, False):
                provided = {key: object() for key, param in signature.parameters.items()
                            if include_defaults or param.default is inspect.Parameter.empty}
                if name == "create_post":
                    provided["reply_receipt_validator"] = None
                    provided["reply_receipts"] = None
                bound = signature.bind(**provided)
                bound.apply_defaults()
                expected = bound.arguments
                assert adapter(**provided) is result
            if name == "create_post":
                assert factories["receipt_values"].call_count == 2
                assert factories["receipts"].call_args_list == [
                    call(values=current["receipt_values"]),
                    call(values=current["receipt_values"]),
                ]
            with pytest.raises(TypeError, match="not_a_public_option"):
                adapter(**provided, not_a_public_option={})
            failure = TypeError("current owner failure")
            patch.setattr(bot, "_post_creation", SimpleNamespace(**{name: Mock(side_effect=failure)}))
            with pytest.raises(TypeError) as caught:
                adapter(**provided)
            assert caught.value is failure


def test_metadata_validation_preserves_exact_form_and_deep_copy(monkeypatch):
    form = {"media_category": "tweet_image", "media_type": "image/png"}
    value = {"request_method": "POST", "request_path": "/2/media/upload", "form": dict(form)}
    copied = Mock(wraps=copy.deepcopy)
    monkeypatch.setattr(owner, "copy", SimpleNamespace(deepcopy=copied))
    result = bot.validate_media_upload_payload_metadata(value, form=form)
    copied.assert_called_once_with(value)
    assert copied.call_args.args[0] is value
    assert result == value and result is not value
    assert result["form"] is not value["form"]
    with pytest.raises(ValueError, match="fields are invalid"):
        bot.validate_media_upload_payload_metadata({**value, "retired_metadata": {}}, form=form)
    with pytest.raises(ValueError, match="changed its remote form"):
        bot.validate_media_upload_payload_metadata({**value, "form": {}}, form=form)


def test_metadata_form_copy_precedes_value_validation_and_preserves_native_errors():
    failure = OSError("form iteration failed")

    class BrokenForm:
        def __iter__(self):
            raise failure

    with pytest.raises(OSError) as caught:
        bot.validate_media_upload_payload_metadata(object(), form=BrokenForm())
    assert caught.value is failure
    with pytest.raises(TypeError, match="is not an object"):
        bot.validate_media_upload_payload_metadata(object(), form={})
    with pytest.raises(ValueError, match="fields are invalid"):
        bot.validate_media_upload_payload_metadata({"request_method": "wrong"}, form={})


def test_metadata_builder_keeps_shallow_form_and_original_validator_reference(monkeypatch):
    form = {"nested": []}
    result = object()

    def validate(value, *, form):
        assert form is original_form
        assert value["form"] is not form
        assert value["form"]["nested"] is form["nested"]
        assert set(value) == {"request_method", "request_path", "form"}
        return result

    original_form = form
    callback = Mock(side_effect=validate)
    monkeypatch.setattr(owner, "validate_media_upload_payload_metadata", callback)
    assert bot.media_upload_payload_metadata(form) is result
    callback.assert_called_once()


@pytest.mark.parametrize("metadata, raw_id", [(None, 0), ({"local": []}, " nonnumeric-id ")])
def test_v2_upload_keeps_optional_metadata_multipart_and_authority_references(monkeypatch, metadata, raw_id):
    events = Mock()
    payload = SimpleNamespace(basename="image.png", data=b"image", mime_type="image/png")
    authority = object()
    validated = {"validated": []}
    events.validate.return_value = validated
    events.request.return_value = {"data": {"id": raw_id}}
    monkeypatch.setattr(bot, "log", events.log)
    monkeypatch.setattr(owner, "validate_media_upload_payload_metadata", events.validate)
    monkeypatch.setattr(bot, "x_request", events.request)
    assert bot.upload_media_v2(authority=authority, payload=payload, payload_metadata=metadata) == str(raw_id).strip()
    assert [c[0] for c in events.mock_calls] == (["log.info", "validate", "request", "log.info"]
            if metadata is not None else ["log.info", "request", "log.info"])
    args, options = events.request.call_args
    assert args == ("POST", "/2/media/upload")
    assert options["files"] == {"media": (payload.basename, payload.data, payload.mime_type)}
    assert all(a is b for a, b in zip(options["files"]["media"], (payload.basename, payload.data, payload.mime_type)))
    assert options["data"] == {"media_category": "tweet_image", "media_type": payload.mime_type}
    assert options["_remote_write_authorization"] is authority
    assert options["_remote_media_payload"] is payload
    assert options["ambiguous_write"] is True
    if metadata is None:
        assert set(options) == {"files", "data", "ambiguous_write", "_remote_write_authorization", "_remote_media_payload"}
        events.validate.assert_not_called()
    else:
        assert options["_remote_media_payload_metadata"] is validated
        assert events.validate.call_args.args[0] is metadata
        assert events.validate.call_args.kwargs["form"] is options["data"]


def test_v2_upload_rejects_boolean_id_and_preserves_native_string_error(monkeypatch):
    payload = SimpleNamespace(basename="image", data=b"image", mime_type="image/jpeg")
    request = Mock(return_value={"data": {"id": True}})
    monkeypatch.setattr(bot, "x_request", request)
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="valid data.id"):
        bot.upload_media_v2(authority=object(), payload=payload)
    failure = OSError("id conversion failed")

    class BrokenId(str):
        def __str__(self):
            raise failure

    request.return_value = {"data": {"id": BrokenId("id")}}
    with pytest.raises(OSError) as caught:
        bot.upload_media_v2(authority=object(), payload=payload)
    assert caught.value is failure
    assert request.call_count == 2


def _media_boundary(monkeypatch, tmp_path):
    events = Mock()
    state = SimpleNamespace(path=tmp_path / "media.json", image=str(tmp_path / "image.unknown"),
                            metadata={"local": []}, authority=object(), payload=object(),
                            guard=object(), mutation=object(), media_id="media-id")
    callbacks = {
        "require_remote_operation_unpaused": ("pause", None),
        "block_if_ambiguous_remote_post": ("barrier", None),
        "media_upload_payload_metadata": ("metadata", state.metadata),
        "begin_media_upload": ("begin", state.authority),
        "bind_media_upload_payload": ("bind", state.payload),
        "begin_confirmed_post_sigint_deferral": ("guard", state.guard),
        "end_confirmed_post_sigint_deferral": ("end", None),
        "upload_media_v2": ("upload", state.media_id),
        "transaction_mutation_authority": ("mutation", state.mutation),
        "confirm_media_upload": ("confirm", None),
        "abort_untransmitted_media_upload": ("abort", None),
        "record_ambiguous_remote_post": ("marker", None),
    }
    for root_name, (name, value) in callbacks.items():
        callback = getattr(events, name)
        callback.return_value = value
        target = owner if root_name == "media_upload_payload_metadata" else bot
        monkeypatch.setattr(target, root_name, callback)
    events.mime.return_value = (None, None)
    monkeypatch.setattr(bot, "mimetypes", SimpleNamespace(guess_type=events.mime))
    monkeypatch.setattr(bot, "log", events.log)
    monkeypatch.setattr(bot, "MEDIA_UPLOAD_RECEIPT_FILE", state.path)
    return events, state


def test_outer_media_binding_validation_guard_confirmation_order(monkeypatch, tmp_path):
    events, state = _media_boundary(monkeypatch, tmp_path)
    assert bot.upload_media(state.image, lane="quote_image") is state.media_id
    form = {"media_category": "tweet_image", "media_type": "image/jpeg"}
    upload = {"authority": state.authority, "payload": state.payload}
    assert events.mock_calls == [
        call.pause("X media upload"), call.barrier(), call.mime(state.image),
        call.metadata(form),
        call.begin(receipt_path=state.path, image_path=Path(state.image), lane="quote_image",
                   mime_type="image/jpeg", payload_metadata=state.metadata),
        call.bind(state.path, state.authority, image_path=Path(state.image), lane="quote_image",
                  mime_type="image/jpeg", payload_metadata=state.metadata),
        call.guard(), call.upload(**upload),
        call.mutation("media upload confirmation"),
        call.confirm(state.path, state.authority, mutation_authority=state.mutation, media_id=state.media_id),
        call.end(state.guard),
    ]
    assert events.begin.call_args.kwargs["payload_metadata"] is state.metadata
    assert events.bind.call_args.kwargs["payload_metadata"] is state.metadata
    assert events.upload.call_args.kwargs["payload"] is state.payload


def test_image_preflight_failure_never_reaches_upload_or_creates_an_ambiguity(monkeypatch, tmp_path):
    events, state = _media_boundary(monkeypatch, tmp_path)
    failure = bot.MediaUploadPreflightError("source image is not durably present")
    events.begin.side_effect = failure
    with pytest.raises(bot.MediaUploadPreflightError) as caught:
        bot.upload_media(state.image, lane="daily_meme")
    assert caught.value is failure
    for name in ("bind", "guard", "upload", "confirm", "abort", "marker"):
        getattr(events, name).assert_not_called()


@pytest.mark.parametrize("boundary", ["begin", "bind"])
def test_uncertain_receipt_setup_remains_ambiguous(monkeypatch, tmp_path, boundary):
    events, state = _media_boundary(monkeypatch, tmp_path)
    failure = bot.MediaUploadReceiptError("unsafe durable transaction directory")
    getattr(events, boundary).side_effect = failure
    with pytest.raises(bot.AmbiguousRemotePostOutcome) as caught:
        bot.upload_media(state.image, lane="daily_meme")
    assert caught.value.__cause__ is failure
    events.upload.assert_not_called()
    events.abort.assert_not_called()


@pytest.mark.parametrize("boundary", ["guard", "confirm"])
def test_media_guard_start_and_confirmation_keep_original_error_scopes(monkeypatch, tmp_path, boundary):
    events, state = _media_boundary(monkeypatch, tmp_path)
    failure = bot.MediaUploadReceiptError("injected receipt failure")
    getattr(events, boundary).side_effect = failure
    with pytest.raises(bot.MediaUploadReceiptError if boundary == "guard" else bot.AmbiguousRemotePostOutcome) as caught:
        bot.upload_media(state.image, lane="daily_meme")
    if boundary == "guard":
        assert caught.value is failure
        events.marker.assert_not_called()
        events.end.assert_not_called()
        events.upload.assert_not_called()
    else:
        assert caught.value.__cause__ is failure
        assert events.mock_calls[-2:] == [call.marker({"text": "", "media": {"media_ids": []}}), call.end(state.guard)]


def test_handoff_checks_confirmation_and_metadata_before_binding_exact_references(monkeypatch, tmp_path):
    events = Mock()
    path, source_path = tmp_path / "media.json", tmp_path / "main.json"
    attempt = {"lane": "quote_image", "attempt_id": "attempt"}
    confirmation = SimpleNamespace(media_id="media-id")
    authority = SimpleNamespace(journal_path=str(tmp_path / "journal"), fence_path=str(tmp_path / "fence"))
    events.load.return_value = confirmation
    events.path.return_value = source_path
    handoff, mutation = object(), object()
    events.bind.return_value, events.mutation.return_value = handoff, mutation
    for root_name, name in {
        "load_confirmed_media_upload": "load", "validate_confirmed_media_upload_metadata": "metadata",
        "bind_media_handoff_to_transport": "bind", "transaction_mutation_authority": "mutation",
        "retire_confirmed_media_upload": "retire", "log": "log",
    }.items():
        monkeypatch.setattr(bot, root_name, getattr(events, name))
    monkeypatch.setattr(
        MainPostReceipts, "attempt_path",
        lambda self, value: events.path(value),
    )
    monkeypatch.setattr(
        bot, "main_post_attempt_path",
        Mock(side_effect=AssertionError("media handoff used root path relay")),
    )
    monkeypatch.setattr(bot, "MEDIA_UPLOAD_RECEIPT_FILE", path)
    assert bot.handoff_confirmed_media_upload_to_main_attempt(attempt, authority) is None
    assert [c[0] for c in events.mock_calls] == ["load", "metadata", "path", "bind", "mutation", "retire", "log.warning"]
    events.bind.assert_called_once_with(path, confirmation, transport_journal_path=Path(authority.journal_path),
                                       transport_fence_path=Path(authority.fence_path), source_receipt_path=source_path)
    assert events.bind.call_args.args[1] is confirmation
    assert events.path.call_args.args[0] is attempt
    assert events.bind.call_args.kwargs["source_receipt_path"] is source_path
    events.retire.assert_called_once_with(path, handoff, mutation_authority=mutation)
    events.reset_mock()
    events.metadata.side_effect = bot.MediaUploadReceiptError("changed metadata")
    with pytest.raises(bot.MediaUploadReceiptError, match="changed metadata"):
        bot.handoff_confirmed_media_upload_to_main_attempt(attempt, object())
    assert [c[0] for c in events.mock_calls] == ["load", "metadata"]
    events.reset_mock()
    events.load.return_value = None
    with pytest.raises(bot.MediaUploadReceiptError, match="no confirmed"):
        bot.handoff_confirmed_media_upload_to_main_attempt(attempt, object())
    assert events.mock_calls == [call.load(path)]


def _public_boundary(monkeypatch, tmp_path):
    events = Mock()

    class TrackedAttempt(dict):
        def clear(self):
            events.clear()
            super().clear()

        def update(self, value):
            events.update(value)
            super().update(value)

    state = SimpleNamespace(attempt=TrackedAttempt(lifecycle_state="sending", lane="quote_image", old=[]),
                            promoted={"lifecycle_state": "attempting", "lane": "quote_image"},
                            path=tmp_path / "main.json", source=object(), text=object(),
                            frozen={"text": "frozen", "media": {"media_ids": ["17"]}},
                            prepared=SimpleNamespace(journal_path=str(tmp_path / "journal")),
                            armed=SimpleNamespace(journal_path=str(tmp_path / "journal"), transaction_id="transaction"),
                            response={"data": {"id": 123}}, mutations=[object(), object()])
    callbacks = {
        "block_if_ambiguous_remote_post": ("barrier", None),
        "freeze_tweet_request": ("freeze", SimpleNamespace(payload=events.payload)),
        "require_instance_lock_for_remote_write": ("lock", None),
        "block_if_remote_write_safety_incident_latched": ("latch", None),
        "global_remote_writes_paused": ("pause", False),
        "bind_lane_transport_source": ("source", state.source),
        "begin_transport_transaction": ("begin", state.prepared),
        "arm_transport_transaction": ("arm", state.armed),
        "transaction_mutation_authority": ("mutation", None),
        "x_request": ("request", state.response),
        "valid_post_id": ("valid_id", True),
        "confirmation_epoch_after_remote_success": ("epoch", 2_000_000_000),
        "confirm_transport_transaction": ("confirm", None),
        "record_ambiguous_remote_post": ("marker", None),
        "retire_consumed_transport_transaction_after_proved_remote_non_success": ("retire", None),
        "abort_untransmitted_transport_transaction": ("abort", None),
    }
    for root_name, (name, value) in callbacks.items():
        callback = getattr(events, name)
        callback.return_value = value
        monkeypatch.setattr(owner if root_name == "valid_post_id" else bot, root_name, callback)
    for owner_type, method, callback, value in (
        (MainPostReceiptValues, "current_attempt_is_valid", events.validate, True),
        (MainPostReceiptValues, "attempt_binds_payload", events.binds, True),
        (MainPostReceipts, "mark_attempting", events.promote, state.promoted),
        (MainPostReceipts, "attempt_path", events.path, state.path),
    ):
        callback.return_value = value
        monkeypatch.setattr(owner_type, method, callback)
    for obsolete in (
        "current_main_post_attempt_is_semantically_valid",
        "main_post_attempt_binds_payload",
        "mark_main_post_attempt_attempting",
        "main_post_attempt_path",
    ):
        monkeypatch.setattr(
            bot, obsolete,
            Mock(side_effect=AssertionError(f"post creation bounced through {obsolete}")),
        )
    events.payload.return_value = state.frozen
    events.mutation.side_effect = state.mutations
    monkeypatch.setattr(bot, "log", events.log)
    return events, state


def test_public_create_preserves_freeze_attempt_mutation_and_confirmation_order(monkeypatch, tmp_path):
    events, state = _public_boundary(monkeypatch, tmp_path)
    assert bot.create_post(state.text, [17], made_with_ai=object(), prepared_main_post_attempt=state.attempt) is state.response
    assert [c[0] for c in events.mock_calls] == ["validate", "barrier", "freeze", "payload", "binds", "lock", "latch", "pause",
            "promote", "clear", "update", "path", "source", "begin", "mutation", "arm", "log.info", "request",
            "valid_id", "mutation", "epoch", "confirm", "log.info"]
    raw = events.freeze.call_args.kwargs
    assert raw == {"method": "POST", "request_path": "/2/tweets",
                   "payload": {"text": state.text, "media": {"media_ids": ["17"]}, "made_with_ai": True}}
    assert raw["payload"]["text"] is state.text
    assert state.attempt == state.promoted and "old" not in state.attempt
    for callback in (events.validate, events.promote, events.path, events.epoch):
        assert callback.call_args.args[0] is state.attempt
    assert events.update.call_args.args[0] is state.promoted
    assert events.binds.call_args.args[1] is state.frozen
    assert events.source.call_args.kwargs["receipt"] is state.attempt
    assert events.source.call_args.kwargs["payload"] is state.frozen
    assert events.begin.call_args.kwargs["source_binding"] is state.source
    events.arm.assert_called_once_with(Path(state.prepared.journal_path), state.prepared, mutation_authority=state.mutations[0])
    events.request.assert_called_once_with("POST", "/2/tweets", json=state.frozen, ambiguous_write=True, _remote_write_authorization=state.armed)
    assert events.request.call_args.kwargs["json"] is state.frozen
    assert events.request.call_args.kwargs["_remote_write_authorization"] is state.armed
    events.valid_id.assert_called_once_with(123)
    events.confirm.assert_called_once_with(Path(state.armed.journal_path), state.armed,
                                           mutation_authority=state.mutations[1], post_id="123", confirmation_epoch=2_000_000_000)
    assert events.log.info.call_args.args[-1] is state.response


def test_public_count_and_callback_gates_and_call_time_historical_import(monkeypatch):
    barrier = Mock(side_effect=ValueError("existing barrier"))
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", barrier)
    monkeypatch.setattr(bot, "_reply_remote_write_barrier", Mock(return_value=barrier))
    with pytest.raises(ValueError, match="existing barrier"):
        bot.create_post("text", prepared_conversational_reply_receipt={}, prepared_main_post_attempt={},
                        on_remote_transaction_started=object())
    barrier.assert_called_once_with()
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="Only a historical-context"):
        bot.create_post("text", prepared_main_post_attempt={}, on_remote_transaction_started=object())
    validator = Mock(return_value=False)
    monkeypatch.setitem(sys.modules, "historical_context_formatter",
                        SimpleNamespace(HistoricalContextReplyStore=SimpleNamespace(_valid_sending_receipt=validator)))
    receipt = {"parent_post_id": "11", "reply_text": "text"}
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="Prepared historical-context"):
        bot.create_post("text", reply_to_id="11", prepared_historical_context_reply_receipt=receipt)
    assert validator.call_args.args[0] is receipt
    assert barrier.call_count == 1


@pytest.mark.parametrize("retirement_error", [None, OSError("retirement failed"), KeyboardInterrupt("retirement interrupted")])
def test_public_rejection_retirement_preserves_proof_marker_and_baseexception(monkeypatch, tmp_path, retirement_error):
    events, state = _public_boundary(monkeypatch, tmp_path)

    class Rejection(Exception):
        pass

    rejection = Rejection("issued rejection")
    rejection.remote_non_success_proof = object()
    monkeypatch.setattr(bot, "ProvedRemotePostNonSuccess", Rejection)
    events.request.side_effect = rejection
    events.retire.side_effect = retirement_error
    expected = bot.AmbiguousRemotePostOutcome if isinstance(retirement_error, Exception) else type(retirement_error or rejection)
    with pytest.raises(expected) as caught:
        bot.create_post(state.text, [17], prepared_main_post_attempt=state.attempt)
    options = events.retire.call_args.kwargs
    assert options["source_binding"] is state.source
    assert options["authority"] is state.armed
    assert options["remote_non_success_proof"] is rejection.remote_non_success_proof
    assert options["mutation_authority"] is state.mutations[1]
    assert [c[0] for c in events.mock_calls][-3:] == (["mutation", "retire", "marker"] if retirement_error else ["request", "mutation", "retire"])
    if retirement_error:
        assert events.marker.call_args.args[0] is state.frozen
    else:
        events.marker.assert_not_called()
    if isinstance(retirement_error, Exception):
        assert caught.value.__cause__ is retirement_error
    else:
        assert caught.value is (retirement_error or rejection)
    events.confirm.assert_not_called()
    events.abort.assert_not_called()


@pytest.mark.parametrize("boundary", ["freeze", "confirm"])
def test_public_native_freeze_and_confirmation_error_scopes(monkeypatch, tmp_path, boundary):
    events, state = _public_boundary(monkeypatch, tmp_path)
    failure = TypeError("native freeze failure") if boundary == "freeze" else bot.TransportJournalError("confirmation failed")
    getattr(events, boundary).side_effect = failure
    with pytest.raises(TypeError if boundary == "freeze" else bot.AmbiguousRemotePostOutcome) as caught:
        bot.create_post(state.text, [17], prepared_main_post_attempt=state.attempt)
    if boundary == "freeze":
        assert caught.value is failure
        assert [c[0] for c in events.mock_calls] == ["validate", "barrier", "freeze"]
    else:
        assert caught.value.__cause__ is failure
        assert [c[0] for c in events.mock_calls][-4:] == ["mutation", "epoch", "confirm", "marker"]
        assert events.marker.call_args.args[0] is state.frozen

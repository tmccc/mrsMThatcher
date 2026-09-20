"""Direct contracts for one main-post publication, without runtime providers."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from mrs_bot_main_post_publication import MainPostPublication
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    configure_simple_quote_post,
    isolate_bot_runtime,  # noqa: F401
)


class AmbiguousOutcome(RuntimeError):
    """Preserve the production exception's service keyword in local assertions."""

    def __init__(self, message, *, service):
        super().__init__(message)
        self.service = service


@pytest.fixture
def publication(tmp_path):
    """Bind inert callbacks and a private path without invoking the bot runtime."""
    trace = Mock()
    trace.proves_non_success.return_value = False
    owner = MainPostPublication(
        lane="quote_image",
        receipt_path=tmp_path / "receipt.json",
        log=Mock(),
        write_attempt=trace.write,
        prepare_transport=trace.prepare,
        handoff_media=trace.handoff,
        begin_sigint=trace.begin,
        create_post=trace.create,
        proves_non_success=trace.proves_non_success,
        retire_attempt=trace.retire,
        end_sigint=trace.end,
        ambiguous_outcome=AmbiguousOutcome,
        incident_latched=trace.incident,
        durable_barrier_exists=trace.barrier,
        retain_sigint=trace.retain,
        inspect_confirmation=trace.inspect,
        journal_path=trace.journal_path,
        confirmation_epoch=trace.epoch,
        build_pending=trace.build,
        promote_pending=trace.promote,
        finalize_pending=trace.finalize,
        run_stage=None,
        validate_meme_post_id=trace.validate,
    )
    return owner, trace


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, dataclasses, io, logging, os, random, re, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('main-post publication import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_main_post_publication', 'mrs_bot_receipt_primitives'}:
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.stat = os.lstat = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = logging.getLogger = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_main_post_publication
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'openai' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_construction_is_inert_and_progress_starts_unavailable(publication):
    owner, trace = publication
    assert not trace.mock_calls
    assert not owner.log.mock_calls
    assert not owner.pending_available
    assert owner.pending_promoted is False
    assert owner.guard is None
    assert not owner.receipt_path.exists()


@pytest.mark.parametrize("failure_stage", ["write", "prepare", "handoff"])
def test_preparation_preserves_native_failure_and_exact_partial_attempt(publication, failure_stage):
    owner, trace = publication
    original = {"before": []}
    prepared = {"after": []}
    source, authority = object(), object()
    trace.prepare.return_value = (prepared, source, authority)
    failure = KeyboardInterrupt(failure_stage)
    getattr(trace, failure_stage).side_effect = failure
    with pytest.raises(KeyboardInterrupt) as caught:
        owner.prepare(original)
    assert caught.value is failure
    trace.write.assert_called_once_with(original)
    assert trace.write.call_args.args[0] is original
    expected = ["write"]
    if failure_stage != "write":
        expected.append("prepare")
        assert trace.prepare.call_args.args[0] is original
    if failure_stage == "handoff":
        expected.append("handoff")
        assert owner.attempt is prepared
        assert owner.transport_source is source
        assert owner.transport_authority is authority
        assert trace.handoff.call_args.args[0] is prepared
        assert trace.handoff.call_args.args[1] is authority
    else:
        assert owner.attempt is original
    assert [entry[0] for entry in trace.mock_calls] == expected
    assert owner.guard is None


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_preparation_and_send_keep_lane_stages_and_payload_references(publication, lane):
    owner, trace = publication
    owner.lane = lane
    stages = []

    def stage(name, operation):
        stages.append(name)
        return operation()

    owner.run_stage = stage if lane == "daily_meme" else None
    attempt, prepared = {"initial": []}, {"bound": []}
    source, authority, guard = object(), object(), object()
    text, media_id, ai = object(), object(), object()
    posted_id = "970001"
    trace.prepare.return_value = (prepared, source, authority)
    trace.begin.return_value = guard
    trace.create.return_value = {"data": {"id": posted_id}}
    owner.prepare(attempt)
    owner.begin_guard()
    assert owner.send(text=text, media_id=media_id, made_with_ai=ai) is posted_id
    assert owner.guard is guard
    trace.create.assert_called_once_with(
        text=text, media_ids=[media_id], reply_to_id=None, made_with_ai=ai,
        prepared_main_post_attempt=prepared,
        prepared_transport_authority=authority,
        prepared_transport_source=source,
    )
    payload = trace.create.call_args.kwargs
    assert payload["text"] is text and payload["media_ids"][0] is media_id
    assert payload["made_with_ai"] is ai
    assert payload["prepared_main_post_attempt"] is prepared
    assert payload["prepared_transport_source"] is source
    assert payload["prepared_transport_authority"] is authority
    assert [entry[0] for entry in trace.mock_calls] == [
        "write", "prepare", "handoff", "begin", "create",
        *(["validate"] if lane == "daily_meme" else []),
    ]
    if lane == "daily_meme":
        assert stages == [
            "main_post_attempt_persistence", "tweet_transport_preparation",
            "media_upload_handoff", "x_post_request", "x_post_response_validation",
        ]
        trace.validate.assert_called_once_with(posted_id)
        owner.log.debug.assert_called_once_with("Posted meme id=%s", posted_id)
    else:
        assert not stages
        trace.validate.assert_not_called()
        owner.log.debug.assert_called_once_with("Posted_id=%s", posted_id)
    trace.end.assert_not_called()
    trace.retain.assert_not_called()


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_send_keeps_validation_failure_without_releasing_guard(publication, lane):
    owner, trace = publication
    owner.lane = lane
    owner.attempt, owner.transport_source, owner.transport_authority = {}, object(), object()
    guard = owner.guard = object()
    trace.create.return_value = {"data": {"id": "invalid"}}
    failure = TypeError("current meme validator")
    trace.validate.side_effect = failure
    expected = RuntimeError if lane == "quote_image" else TypeError
    with pytest.raises(expected) as caught:
        owner.send(text="text", media_id="media", made_with_ai=False)
    if lane == "quote_image":
        assert str(caught.value) == "Quote/image post did not return a valid post id; used histories unchanged"
        trace.validate.assert_not_called()
    else:
        assert caught.value is failure
        trace.validate.assert_called_once_with("invalid")
    assert owner.guard is guard
    trace.end.assert_not_called()


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_confirmation_checks_journal_before_epoch_and_keeps_epoch_reference(publication, lane):
    owner, trace = publication
    owner.lane = lane
    attempt = owner.attempt = {"exact": []}
    journal, raw_epoch, result_epoch = object(), object(), object()
    trace.journal_path.return_value = journal
    trace.inspect.return_value = SimpleNamespace(post_id="123", confirmation_epoch=raw_epoch)
    trace.epoch.return_value = result_epoch
    assert owner.read_confirmation_epoch(123) is result_epoch
    assert trace.mock_calls == [
        call.journal_path(owner.receipt_path), call.inspect(journal), call.epoch(attempt, raw_epoch),
    ]
    assert trace.epoch.call_args.args[0] is attempt
    assert trace.epoch.call_args.args[1] is raw_epoch
    trace.reset_mock()
    trace.inspect.return_value.post_id = "different"
    with pytest.raises(AmbiguousOutcome) as caught:
        owner.read_confirmation_epoch(123)
    label = "regular" if lane == "quote_image" else "meme"
    assert str(caught.value) == f"Confirmed {label}-post identity differs from its journal"
    assert caught.value.service == "x"
    trace.epoch.assert_not_called()


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
@pytest.mark.parametrize("failure_stage", ["build", "promote", "finalize"])
def test_pending_progress_distinguishes_none_and_completed_promotion(publication, lane, failure_stage):
    owner, trace = publication
    owner.lane = lane
    attempt = owner.attempt = {"exact": []}
    guard = owner.guard = object()
    epoch, summary = object(), object()
    trace.build.return_value = None
    trace.promote.return_value = None
    failure = KeyboardInterrupt(failure_stage)
    getattr(trace, failure_stage).side_effect = failure
    assert not owner.pending_available
    with pytest.raises(KeyboardInterrupt) as caught:
        owner.confirm_pending_schedule(123, epoch, image_summary=summary)
    assert caught.value is failure
    assert owner.pending_available is (failure_stage != "build")
    assert owner.pending_promoted is (failure_stage == "finalize")
    if failure_stage != "build":
        assert owner.pending_receipt is None
    options = {"post_id": "123", "confirmation_epoch": epoch}
    if lane == "daily_meme":
        options["image_summary"] = summary
    expected = [call.build(attempt, **options)]
    if failure_stage != "build":
        expected.append(call.promote(attempt, **options))
    if failure_stage == "finalize":
        expected.append(call.finalize(None))
    assert trace.mock_calls == expected
    assert trace.build.call_args.args[0] is attempt
    assert trace.build.call_args.kwargs["confirmation_epoch"] is epoch
    if lane == "daily_meme":
        assert trace.build.call_args.kwargs["image_summary"] is summary
    assert owner.guard is guard
    trace.end.assert_not_called()


def test_successful_confirmation_preserves_promoted_and_final_result_objects(publication):
    owner, trace = publication
    owner.attempt = {"exact": []}
    built, promoted, final = {"built": []}, {"promoted": []}, {"final": []}
    trace.build.return_value, trace.promote.return_value, trace.finalize.return_value = built, promoted, final
    guard = owner.guard = object()
    assert owner.confirm_pending_schedule("123", 1234) is final
    assert owner.pending_receipt is promoted
    assert trace.finalize.call_args.args[0] is promoted
    assert owner.pending_promoted is True
    assert owner.guard is guard
    trace.end.assert_not_called()


def test_guard_methods_keep_none_calls_retention_and_native_release_failure(publication):
    owner, trace = publication
    owner.release_guard()
    trace.end.assert_called_once_with(None)
    trace.reset_mock()
    guard = object()
    trace.begin.return_value = guard
    owner.begin_guard()
    owner.retain_guard()
    assert owner.guard is guard
    trace.retain.assert_called_once_with(lane="quote_image", guard=guard)
    failure = KeyboardInterrupt("deferred stop")
    trace.end.side_effect = failure
    with pytest.raises(KeyboardInterrupt) as caught:
        owner.release_guard()
    assert caught.value is failure
    assert owner.guard is guard
    trace.end.side_effect = None
    owner.release_guard()
    assert owner.guard is None
    assert trace.end.call_args_list == [call(guard), call(guard)]


def test_failure_before_attempt_skips_classification_but_releases_none(publication):
    owner, trace = publication
    owner.handle_remote_failure(KeyboardInterrupt("selection"))
    assert trace.mock_calls == [call.end(None)]


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_failed_retirement_releases_before_wrapping_with_original_cause(publication, lane):
    owner, trace = publication
    owner.lane = lane
    attempt = owner.attempt = {"exact": []}
    guard = owner.guard = object()
    remote_failure = ValueError("definite remote failure")
    removal_failure = KeyboardInterrupt("retirement interrupted")
    trace.proves_non_success.return_value = True
    trace.retire.side_effect = removal_failure
    with pytest.raises(AmbiguousOutcome) as caught:
        owner.handle_remote_failure(remote_failure)
    assert caught.value.__cause__ is removal_failure
    assert caught.value.service == "x"
    label = "regular" if lane == "quote_image" else "meme"
    assert str(caught.value) == f"A definitely unsuccessful {label} post left its durable sending receipt unresolved"
    assert trace.mock_calls == [
        call.proves_non_success(remote_failure),
        call.retire(attempt, sending_disposition="definite_non_success"),
        call.end(guard),
    ]
    assert trace.retire.call_args.args[0] is attempt
    assert owner.guard is None


@pytest.mark.parametrize("incident,barrier", [(False, False), (True, False), (True, True)])
def test_ambiguous_failure_retains_only_without_durable_barrier(publication, incident, barrier):
    owner, trace = publication
    owner.attempt = {}
    guard = owner.guard = object()
    failure = AmbiguousOutcome("uncertain", service="x")
    trace.incident.return_value = incident
    trace.barrier.return_value = barrier
    owner.handle_remote_failure(failure)
    expected = [call.proves_non_success(failure), call.incident()]
    if incident:
        expected.append(call.barrier())
    if incident and not barrier:
        expected.append(call.retain(lane="quote_image", guard=guard))
        assert owner.guard is guard
    else:
        expected.append(call.end(guard))
        assert owner.guard is None
    assert trace.mock_calls == expected


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_root_factory_binds_current_callbacks_and_independent_progress(monkeypatch, lane):
    bindings = {
        "log": "log",
        "write_attempt": "write_main_post_attempt",
        "prepare_transport": "prepare_main_tweet_transport",
        "handoff_media": "handoff_confirmed_media_upload_to_main_attempt",
        "begin_sigint": "begin_confirmed_post_sigint_deferral",
        "create_post": "create_post",
        "proves_non_success": "api_error_proves_remote_non_success",
        "retire_attempt": "remove_main_post_attempt",
        "end_sigint": "end_confirmed_post_sigint_deferral",
        "ambiguous_outcome": "AmbiguousRemotePostOutcome",
        "incident_latched": "remote_write_safety_incident_is_latched",
        "durable_barrier_exists": "durable_remote_write_safety_barrier_exists",
        "retain_sigint": "retain_sigint_deferral_without_durable_barrier",
        "inspect_confirmation": "inspect_confirmed_transport_transaction",
        "journal_path": "journal_path_for_receipt",
        "confirmation_epoch": "confirmation_epoch_for_main_attempt",
        "build_pending": "build_confirmed_pending_schedule_receipt",
        "promote_pending": "promote_main_post_attempt_to_confirmed_pending_schedule",
        "finalize_pending": "finalize_confirmed_pending_schedule_receipt",
    }
    stage, validator = Mock(), Mock()
    monkeypatch.setattr(bot, "run_daily_meme_stage", stage)
    monkeypatch.setattr(bot, "require_valid_meme_post_id", validator)
    owners = []
    for _ in range(2):
        current = {field: Mock() for field in bindings}
        for field, root_name in bindings.items():
            monkeypatch.setattr(bot, root_name, current[field])
        path = object()
        path_name = "REGULAR_POST_RECEIPT_FILE" if lane == "quote_image" else "MEME_POST_RECEIPT_FILE"
        monkeypatch.setattr(bot, path_name, path)
        owner = bot._main_post_publication_owner(lane)
        owners.append(owner)
        assert owner.lane is lane and owner.receipt_path is path
        assert all(getattr(owner, field) is value for field, value in current.items())
        assert owner.run_stage is (stage if lane == "daily_meme" else None)
        assert owner.validate_meme_post_id is (validator if lane == "daily_meme" else None)
        assert owner.guard is None and not owner.pending_available
        assert owner.pending_promoted is False
        assert all(not value.mock_calls for value in current.values())
        owner.guard, owner.pending_receipt, owner.pending_promoted = object(), {}, True
    assert owners[0] is not owners[1]
    assert owners[0].create_post is not owners[1].create_post
    assert owners[0].guard is not owners[1].guard
    stage.assert_not_called()
    validator.assert_not_called()


def test_quote_publication_keeps_create_callback_bound_before_upload(tmp_path, monkeypatch):
    lines_used, images_used, state, *_ = configure_simple_quote_post(tmp_path, monkeypatch)
    initial_create = Mock(wraps=bot.create_post)
    later_create = Mock(side_effect=AssertionError("publication rebound create after upload"))
    monkeypatch.setattr(bot, "create_post", initial_create)

    def upload(_path, **_options):
        monkeypatch.setattr(bot, "create_post", later_create)
        return "media-1"

    monkeypatch.setattr(bot, "upload_media", upload)
    bot.post_random_quote(lines_used, images_used, state)
    initial_create.assert_called_once()
    later_create.assert_not_called()
    assert state["last_main_post_id"] == "950001"

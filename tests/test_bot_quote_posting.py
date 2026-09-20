from __future__ import annotations

import inspect
from pathlib import Path
import random
import subprocess
import sys
from unittest.mock import Mock

import pytest

import mrs_bot_quote_posting as posting
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    configure_simple_quote_post,
    isolate_bot_runtime,  # noqa: F401
)


def test_import_needs_no_runtime_access_and_keeps_shared_rng():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, types
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('posting import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai'}:
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
import mrs_bot_quote_posting
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert posting.random is bot.random is random


def test_adapter_passes_current_dependencies_and_references_on_every_call(monkeypatch):
    names = [name for name, parameter in inspect.signature(posting.post_random_quote).parameters.items()
             if parameter.kind == inspect.Parameter.KEYWORD_ONLY]
    assert len(names) == 58
    assert not set(names) & {
        "apply_state_fields", "valid_post_id",
        "main_post_attempt", "pending_schedule_receipt", "quote_post_epoch",
        "fallback_receipt", "quote_schedule_fields", "meme_schedule_fields",
    }
    lines_used, images_used, state, result = set(), set(), {}, object()
    owner = Mock(return_value=result)
    monkeypatch.setattr(posting, "post_random_quote", owner)
    for _ in range(2):
        current = {name: object() for name in names}
        for name, value in current.items():
            monkeypatch.setattr(bot, name, value)
        assert bot.post_random_quote(lines_used, images_used, state) is result
        args, kwargs = owner.call_args
        assert all(actual is expected for actual, expected in zip(args, (lines_used, images_used, state)))
        assert kwargs.keys() == current.keys()
        assert all(kwargs[name] is value for name, value in current.items())
    failure = KeyboardInterrupt("owner failure")
    owner.side_effect = failure
    with pytest.raises(KeyboardInterrupt) as caught:
        bot.post_random_quote(lines_used, images_used, state)
    assert caught.value is failure


@pytest.mark.parametrize("reconciled", [False, True])
def test_preflight_order_and_snapshot_preserve_reconciler_set_references(monkeypatch, reconciled):
    lines_used, images_used, state, events = {"old quote"}, {"old image"}, {}, []
    log = Mock()
    failure = KeyboardInterrupt("outbox interrupted")

    def barrier(**kwargs):
        assert kwargs == {"allow_confirmed_pending_schedule_reconciliation": True}
        events.append("barrier")

    def clock():
        events.append("clock")
        return 1_800_000_000

    def reconcile(lines, images, current_state, **kwargs):
        assert lines is lines_used and images is images_used and current_state is state
        assert kwargs == {"minimum_next_quote_epoch": 1_800_000_000}
        lines.add("reconciled quote")
        images.add("reconciled image")
        events.append("reconcile")
        return {"regular": reconciled}

    def outbox():
        events.append("outbox")
        lines_used.clear()
        images_used.clear()
        raise failure

    monkeypatch.setattr(bot, "log", log)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", barrier)
    monkeypatch.setattr(bot, "now_epoch", clock)
    monkeypatch.setattr(bot, "reconcile_main_post_receipts", reconcile)
    monkeypatch.setattr(bot, "require_historical_context_outbox_writable", outbox)
    monkeypatch.setattr(bot, "choose_regular_quote_image_pair", lambda *a, **kw: pytest.fail("unexpected selection"))
    if reconciled:
        assert bot.post_random_quote(lines_used, images_used, state) is None
        log.warning.assert_called_once_with("Reconciled regular quote/image receipt; not creating a second regular post in the same call")
    else:
        with pytest.raises(KeyboardInterrupt) as caught:
            bot.post_random_quote(lines_used, images_used, state)
        assert caught.value is failure
    assert events == ["barrier", "clock", "reconcile", *([] if reconciled else ["outbox"])]
    assert lines_used == {"old quote", "reconciled quote"}
    assert images_used == {"old image", "reconciled image"}


@pytest.mark.parametrize("daily_meme_enabled", [False, True])
def test_posting_keeps_upload_shape_and_publication_order(
    tmp_path, monkeypatch, daily_meme_enabled,
):
    lines_used, images_used, state, *_ = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", daily_meme_enabled)
    events, captured, draws = [], {}, []
    original_create = bot.create_post
    original_select = bot.choose_regular_quote_image_pair

    def select(lines, images, current_state, **kwargs):
        assert lines is lines_used and images is images_used and current_state is state
        assert kwargs == {}
        return original_select(lines, images, current_state)

    def upload(path, **kwargs):
        assert path == str(tmp_path / "images" / "t01.jpg")
        assert kwargs == {"lane": "quote_image"}
        events.append("upload")
        return "media-1"

    def randint(low, high):
        draws.append((low, high))
        return low

    def create(**kwargs):
        events.append("create")
        captured["create"] = kwargs
        return original_create(**kwargs)

    def track(name):
        original = getattr(bot, name)

        def invoke(*args, **kwargs):
            events.append(name)
            return original(*args, **kwargs)

        monkeypatch.setattr(bot, name, invoke)

    for name in (
        "write_main_post_attempt", "prepare_main_tweet_transport",
        "handoff_confirmed_media_upload_to_main_attempt", "begin_confirmed_post_sigint_deferral",
        "save_regular_post_protected_state", "enqueue_historical_context_obligation",
        "retire_lane_transport_journal_if_present", "remove_regular_post_receipt",
        "emit_account_root_posted",
    ):
        track(name)
    monkeypatch.setattr(bot, "choose_regular_quote_image_pair", select)
    monkeypatch.setattr(bot, "upload_media", upload)
    monkeypatch.setattr(bot.random, "randint", randint)
    monkeypatch.setattr(bot, "create_post", create)
    monkeypatch.setattr(bot, "log_event", lambda event, **fields: events.append(event))
    monkeypatch.setattr(bot, "safely_process_due_historical_context_obligations", lambda **kwargs: events.append("context"))

    bot.post_random_quote(lines_used, images_used, state)

    assert draws == [(bot.POST_SLEEP_MIN, bot.POST_SLEEP_MAX), *(
        [(bot.MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS, bot.MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS)]
        if daily_meme_enabled else []
    )]
    before_create = events[:events.index("create") + 1]
    assert before_create == [
        "upload", "write_main_post_attempt", "prepare_main_tweet_transport",
        "handoff_confirmed_media_upload_to_main_attempt", "begin_confirmed_post_sigint_deferral", "create",
    ]
    ordered = [
        "save_regular_post_protected_state", "enqueue_historical_context_obligation",
        "retire_lane_transport_journal_if_present", "remove_regular_post_receipt",
        "main_post_posted", "emit_account_root_posted", "account_root_posted", "context",
    ]
    assert [events.index(name) for name in ordered] == sorted(events.index(name) for name in ordered)
    assert captured["create"]["text"] == "Good quote."
    assert captured["create"]["made_with_ai"] is False
    assert images_used == {"t01.jpg"} and lines_used == {bot.quote_text_hash("Good quote.")}
    assert state["last_main_post_id"] == "950001"
    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()


@pytest.mark.parametrize("failure_point", ["meme_delay", "quote_log", "attempt_build", "attempt_write"])
def test_prepublication_interrupt_preserves_draws_rollback_and_attempt_ownership(
    tmp_path, monkeypatch, failure_point,
):
    lines_used, images_used, state, *_ = configure_simple_quote_post(tmp_path, monkeypatch)
    lines_used.add("old quote")
    images_used.add("old image")
    failure = KeyboardInterrupt(failure_point)
    draws = []
    log = Mock()
    upload = Mock(return_value="media-1")
    proof = Mock(return_value=False)
    remove = Mock()
    release = Mock()
    transport = Mock(side_effect=AssertionError("unexpected transport preparation"))
    original_write = bot.write_main_post_attempt

    def choose(lines, images, current_state, **kwargs):
        assert lines is lines_used and images is images_used and current_state is state
        lines.clear()
        images.clear()
        return (
            {"line_no": 0, "quote_hash": bot.quote_text_hash("Good quote."), "text": "Good quote."},
            {"image_no": 0, "path": str(tmp_path / "images" / "t01.jpg"), "basename": "t01.jpg"},
            1,
        )

    def randint(low, high):
        draws.append((low, high))
        if failure_point == "meme_delay" and len(draws) == 2:
            raise failure
        return low

    def interrupt(*args, **kwargs):
        raise failure

    def write_then_interrupt(attempt):
        original_write(attempt)
        raise failure

    monkeypatch.setattr(bot, "choose_regular_quote_image_pair", choose)
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot.random, "randint", randint)
    monkeypatch.setattr(bot, "log", log)
    monkeypatch.setattr(bot, "upload_media", upload)
    monkeypatch.setattr(bot, "api_error_proves_remote_non_success", proof)
    monkeypatch.setattr(bot, "remove_main_post_attempt", remove)
    monkeypatch.setattr(bot, "end_confirmed_post_sigint_deferral", release)
    monkeypatch.setattr(bot, "prepare_main_tweet_transport", transport)
    if failure_point == "quote_log":
        log.debug.side_effect = interrupt
    elif failure_point == "attempt_build":
        monkeypatch.setattr(bot, "build_main_post_attempt", interrupt)
    elif failure_point == "attempt_write":
        monkeypatch.setattr(bot, "write_main_post_attempt", write_then_interrupt)

    with pytest.raises(KeyboardInterrupt) as caught:
        bot.post_random_quote(lines_used, images_used, state)

    assert caught.value is failure
    assert draws == [
        (bot.POST_SLEEP_MIN, bot.POST_SLEEP_MAX),
        (bot.MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS, bot.MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS),
    ]
    assert lines_used == {"old quote"} and images_used == {"old image"}
    assert "last_main_post_id" not in state
    release.assert_called_once_with(None)
    remove.assert_not_called()
    transport.assert_not_called()
    assert upload.call_count == int(failure_point in {"attempt_build", "attempt_write"})
    if failure_point == "attempt_write":
        proof.assert_called_once_with(failure)
        assert bot.load_regular_post_receipt()[0] == "sending"
    else:
        proof.assert_not_called()
        assert not bot.REGULAR_POST_RECEIPT_FILE.exists()


@pytest.mark.parametrize("failure_point", ["protected_interrupt", "post_event"])
def test_completion_keeps_signal_release_exception_scope_and_receipt_disposition(
    tmp_path, monkeypatch, failure_point,
):
    lines_used, images_used, state, *_ = configure_simple_quote_post(tmp_path, monkeypatch)
    guard = object()
    release = Mock()
    begin = Mock(return_value=guard)
    create = Mock(wraps=bot.create_post)
    root_event = Mock()
    context = Mock()
    failure = KeyboardInterrupt("completion interrupted") if failure_point == "protected_interrupt" else RuntimeError(failure_point)

    def fail(*args, **kwargs):
        release.assert_called_once_with(guard)
        assert bot.REGULAR_POST_RECEIPT_FILE.exists() is (failure_point == "protected_interrupt")
        raise failure

    def event(name, **fields):
        if name == "main_post_posted":
            fail()

    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", begin)
    monkeypatch.setattr(bot, "end_confirmed_post_sigint_deferral", release)
    monkeypatch.setattr(bot, "create_post", create)
    monkeypatch.setattr(bot, "emit_account_root_posted", root_event)
    monkeypatch.setattr(bot, "safely_process_due_historical_context_obligations", context)
    if failure_point == "protected_interrupt":
        monkeypatch.setattr(bot, "save_regular_post_protected_state", fail)
    else:
        monkeypatch.setattr(bot, "log_event", event)

    with pytest.raises(type(failure)) as caught:
        bot.post_random_quote(lines_used, images_used, state)

    assert caught.value is failure
    assert create.call_count == 1
    begin.assert_called_once_with()
    release.assert_called_once_with(guard)
    root_event.assert_not_called()
    context.assert_not_called()
    assert lines_used == {bot.quote_text_hash("Good quote.")}
    assert images_used == {"t01.jpg"}
    assert state["last_main_post_id"] == "950001"
    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]
    assert bot.REGULAR_POST_RECEIPT_FILE.exists() is (failure_point == "protected_interrupt")

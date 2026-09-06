from __future__ import annotations

import inspect
from pathlib import Path
import random
import subprocess
import sys
from unittest.mock import Mock

import pytest

import mrs_bot_quote_posting as posting
from tests.test_engagement_question_experiment import (
    _install_synthetic_runtime_authority,
    _live_synthetic_bundle,
    _state_at_first_member_with_arm,
    synthetic_plan_bundle,
)
from tests.test_unit_helpers import (
    bot,
    configure_simple_quote_post,
    isolate_regular_post_receipt,
)


def test_import_needs_no_runtime_access_and_keeps_shared_rng():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, types
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('posting import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'engagement_question_experiment', 'requests', 'openai'}:
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
assert 'engagement_question_experiment' not in sys.modules
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
    assert len(names) == 76
    assert not set(names) & {
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
    monkeypatch.setattr(bot, "engagement_question_opportunity", lambda *a, **kw: pytest.fail("unexpected selection"))
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


@pytest.mark.parametrize("arm", ["ordinary", "control", "treatment"])
def test_posting_keeps_closure_objects_upload_shape_and_publication_order(
    tmp_path, monkeypatch, synthetic_plan_bundle, arm,
):
    lines_used, images_used, state, *_ = configure_simple_quote_post(tmp_path, monkeypatch)
    experimental = arm != "ordinary"
    monkeypatch.setattr(bot, "engagement_question_experiment_enabled", experimental)
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", experimental)
    monkeypatch.setattr(bot, "engagement_question_notification_output_path", str(tmp_path / "notification.json"))
    if experimental:
        bundle = _live_synthetic_bundle(synthetic_plan_bundle)
        _install_synthetic_runtime_authority(bundle, monkeypatch)
        experiment_state, epoch = _state_at_first_member_with_arm(bundle, arm)
        state["engagement_question_experiment"] = experiment_state
        monkeypatch.setattr(bot, "now_epoch", lambda: epoch)

    events, captured, draws = [], {}, []
    original_choose_image = bot.choose_engagement_question_image
    original_revalidate = bot.revalidate_or_invalidate_engagement_question_publication
    original_create = bot.create_post

    def choose_image(images, quote, current_state):
        assert images is images_used and current_state is state
        captured["quote"] = quote
        return original_choose_image(images, quote, current_state)

    def revalidate(**kwargs):
        assert kwargs["state"] is state and kwargs["lines_used"] is lines_used
        assert kwargs["envelope"] is captured["envelope"]
        assert kwargs["quote_choice"] is captured["quote"]
        events.append("revalidate")
        return original_revalidate(**kwargs)

    def upload(path, **kwargs):
        assert path == str(tmp_path / "images" / "t01.jpg")
        if experimental:
            assert kwargs.keys() == {"lane", "engagement_experiment", "pre_transport_validation"}
            captured["envelope"] = kwargs["engagement_experiment"]
            validator = kwargs["pre_transport_validation"]
            assert str(inspect.signature(validator)) == "() -> 'None'"
            closure = inspect.getclosurevars(validator).nonlocals
            assert closure["state"] is state and closure["lines_used"] is lines_used
            assert closure["engagement_experiment_envelope"] is captured["envelope"]
            assert closure["quote_choice"] is captured["quote"]
            captured["public_text"] = closure["tweet"]
            validator()
        else:
            assert kwargs == {"lane": "quote_image"}
        events.append("upload")
        return "media-1"

    def randint(low, high):
        draws.append((low, high))
        return low

    def create(**kwargs):
        events.append("create")
        captured["create"] = kwargs
        if experimental:
            assert kwargs["text"] is captured["public_text"]
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
        "publish_pending_engagement_question_notification", "emit_account_root_posted",
    ):
        track(name)
    monkeypatch.setattr(bot, "choose_engagement_question_image", choose_image)
    monkeypatch.setattr(bot, "revalidate_or_invalidate_engagement_question_publication", revalidate)
    monkeypatch.setattr(bot, "upload_media", upload)
    monkeypatch.setattr(bot.random, "randint", randint)
    monkeypatch.setattr(bot, "create_post", create)
    monkeypatch.setattr(bot, "log_event", lambda event, **fields: events.append(event))
    monkeypatch.setattr(bot, "safely_process_due_historical_context_obligations", lambda **kwargs: events.append("context"))

    bot.post_random_quote(lines_used, images_used, state)

    assert draws == [(bot.POST_SLEEP_MIN, bot.POST_SLEEP_MAX), *(
        [(bot.MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS, bot.MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS)]
        if experimental else []
    )]
    before_create = events[:events.index("create") + 1]
    assert before_create == [
        *(["revalidate"] if experimental else []), "upload",
        *(["revalidate"] if experimental else []), "write_main_post_attempt",
        *(["revalidate"] if experimental else []), "prepare_main_tweet_transport",
        "handoff_confirmed_media_upload_to_main_attempt", "begin_confirmed_post_sigint_deferral", "create",
    ]
    ordered = [
        "save_regular_post_protected_state",
        *(["engagement_question_experimental_member_confirmed", "main_post_posted"] if experimental else []),
        "enqueue_historical_context_obligation", "retire_lane_transport_journal_if_present",
        "remove_regular_post_receipt", "publish_pending_engagement_question_notification",
        *([] if experimental else ["main_post_posted"]),
        "emit_account_root_posted", "account_root_posted", "context",
    ]
    assert [events.index(name) for name in ordered] == sorted(events.index(name) for name in ordered)
    assert captured["create"]["made_with_ai"] is False
    assert images_used == {"t01.jpg"} and len(lines_used) == 1
    assert state["last_main_post_id"] == "950001"
    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
    if experimental:
        canonical = captured["quote"]["text"]
        assert captured["envelope"]["canonical_quote_text"] == canonical
        assert (captured["public_text"] != canonical) is (arm == "treatment")
        assert captured["quote"]["quote_hash"] in lines_used
        assert state["engagement_question_experiment"]["confirmed_publications"][-1]["post_id"] == "950001"

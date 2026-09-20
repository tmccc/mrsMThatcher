"""Exercise partial recovery values after a locally confirmed remote post."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import mrs_bot_quote_posting as posting

from mrs_bot_main_post_confirmation_persistence import RegularPostPersistenceResult
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    configure_simple_meme_post,
    configure_simple_quote_post,
    isolate_bot_runtime,  # noqa: F401
    mock_confirmed_main_post,
)


def prepare_post(tmp_path, monkeypatch, lane):
    """Use local transaction fixtures and observe the emergency persistence boundary."""
    if lane == "quote_image":
        lines, images, state, *_ = configure_simple_quote_post(tmp_path, monkeypatch)
        run = lambda: bot.post_random_quote(lines, images, state)
        epoch_key = "last_quote_post_epoch"
        materialiser = "materialize_bound_regular_schedule_receipt"
        complete_name = "confirmed_regular_emergency_representation_is_complete"
    else:
        state, _ = configure_simple_meme_post(tmp_path, monkeypatch)
        monkeypatch.setattr(
            bot, "create_post",
            lambda **kwargs: mock_confirmed_main_post(kwargs, {"data": {"id": "970001"}}),
        )
        run = lambda: bot.post_next_meme(state)
        epoch_key = "last_meme_post_epoch"
        materialiser = "materialize_bound_meme_schedule_receipt"
        complete_name = "confirmed_meme_emergency_representation_is_complete"
    state[epoch_key] = 1_700_000_000
    guard = object()
    release = Mock()
    save = Mock()
    emergency_save = Mock(return_value=RegularPostPersistenceResult((), None))
    complete = Mock(return_value=False)
    latch = Mock(return_value=True)
    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", lambda: guard)
    monkeypatch.setattr(bot, "end_confirmed_post_sigint_deferral", release)
    monkeypatch.setattr(bot, "save_state", save)
    monkeypatch.setattr(bot, "emergency_persist_confirmed_regular_post", emergency_save)
    monkeypatch.setattr(bot, complete_name, complete)
    monkeypatch.setattr(bot, "latch_confirmed_post_persistence_failure", latch)
    return SimpleNamespace(
        run=run, state=state, epoch_key=epoch_key, materialiser=materialiser,
        guard=guard, release=release, save=save, emergency_save=emergency_save,
        complete=complete, latch=latch,
    )


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
@pytest.mark.parametrize("epoch_available", [False, True])
def test_unavailable_confirmation_epoch_differs_from_an_assigned_none(
    tmp_path, monkeypatch, lane, epoch_available,
):
    scenario = prepare_post(tmp_path, monkeypatch, lane)
    epoch = Mock(return_value=None)
    if not epoch_available:
        epoch.side_effect = OSError("epoch unavailable")
    build = Mock(side_effect=OSError("pending receipt unavailable"))
    monkeypatch.setattr(bot, "confirmation_epoch_for_main_attempt", epoch)
    monkeypatch.setattr(bot, "build_confirmed_pending_schedule_receipt", build)
    with pytest.raises(bot.UnrecoverableConfirmedPostPersistenceError):
        scenario.run()
    assert scenario.state[scenario.epoch_key] == (None if epoch_available else 1_700_000_000)
    assert build.call_count == int(epoch_available)
    assert scenario.complete.call_args.kwargs["post_epoch"] is None
    scenario.latch.assert_called_once()
    scenario.release.assert_called_once_with(scenario.guard)


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_assigned_none_pending_receipt_is_still_offered_to_fallback(
    tmp_path, monkeypatch, lane,
):
    scenario = prepare_post(tmp_path, monkeypatch, lane)
    materialise = Mock(side_effect=ValueError("invalid pending receipt"))
    monkeypatch.setattr(bot, "build_confirmed_pending_schedule_receipt", Mock(return_value=None))
    monkeypatch.setattr(
        bot, "promote_main_post_attempt_to_confirmed_pending_schedule",
        Mock(side_effect=OSError("promotion failed")),
    )
    monkeypatch.setattr(bot, scenario.materialiser, materialise)
    with pytest.raises(bot.UnrecoverableConfirmedPostPersistenceError):
        scenario.run()
    materialise.assert_called_once_with(None)
    if lane == "quote_image":
        scenario.emergency_save.assert_called_once()
    else:
        # Meme materialisation and its state update share one exception boundary.
        scenario.save.assert_not_called()
    scenario.release.assert_called_once_with(scenario.guard)


def test_quote_recovery_keeps_receipt_and_quote_schedule_when_meme_projection_fails(
    tmp_path, monkeypatch,
):
    scenario = prepare_post(tmp_path, monkeypatch, "quote_image")
    fallback = {"next_quote_post_epoch": "1800007200", "next_meme_post_epoch": "invalid"}
    apply_fields = Mock(wraps=bot.apply_state_fields)
    monkeypatch.setattr(
        bot, "promote_main_post_attempt_to_confirmed_pending_schedule",
        Mock(side_effect=OSError("promotion failed")),
    )
    monkeypatch.setattr(bot, scenario.materialiser, Mock(return_value=fallback))
    monkeypatch.setattr(posting, "apply_state_fields", apply_fields)
    with pytest.raises(bot.UnrecoverableConfirmedPostPersistenceError):
        scenario.run()
    assert scenario.state["next_quote_post_epoch"] == 1_800_007_200
    apply_fields.assert_called_once_with(scenario.state, {"next_quote_post_epoch": 1_800_007_200})
    assert scenario.latch.call_args.kwargs["failure_components"] == [
        "regular_post_receipt", "bound_schedule_materialisation", "incomplete_regular_post_state",
    ]


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
@pytest.mark.parametrize("failure_stage", ["materialisation", "projection"])
def test_emergency_materialisation_interrupt_preserves_signal_and_save_boundary(
    tmp_path, monkeypatch, lane, failure_stage,
):
    scenario = prepare_post(tmp_path, monkeypatch, lane)
    failure = KeyboardInterrupt("recovery interrupted")

    class InterruptedEpoch:
        def __int__(self):
            raise failure

    materialise = Mock(return_value={
        "next_quote_post_epoch": 1_800_007_200,
        "next_meme_post_epoch": InterruptedEpoch(),
    })
    if failure_stage == "materialisation":
        materialise.side_effect = failure
    monkeypatch.setattr(
        bot, "promote_main_post_attempt_to_confirmed_pending_schedule",
        Mock(side_effect=OSError("promotion failed")),
    )
    monkeypatch.setattr(bot, scenario.materialiser, materialise)
    with pytest.raises(KeyboardInterrupt) as caught:
        scenario.run()
    assert caught.value is failure
    scenario.save.assert_not_called()
    scenario.emergency_save.assert_not_called()
    scenario.release.assert_not_called()
    scenario.latch.assert_not_called()

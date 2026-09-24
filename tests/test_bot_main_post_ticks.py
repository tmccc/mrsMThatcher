"""Exercise scheduling decisions without starting the production main loop."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mrs_bot_main_post_assembly import MainPostAssembly
from tests.helpers.bot_runtime import bot


@pytest.fixture
def tick(monkeypatch):
    context = SimpleNamespace(
        current=10_000,
        state={
            "next_quote_post_epoch": 10_000,
            "next_meme_post_epoch": 10_000,
            "last_quote_post_epoch": 9_910,
        },
        lines={"used quote"},
        images={"used image"},
    )
    for name in (
        "post_random_quote", "post_next_meme", "save_state",
        "report_bot_health_progress", "log",
    ):
        value = Mock()
        setattr(context, name, value)
        monkeypatch.setattr(bot, name, value)
    context.controls = Mock()
    context.controls.lane_paused.return_value = False
    context.lane_paused = context.controls.lane_paused
    monkeypatch.setattr(bot, "_runtime_controls_owner", Mock(return_value=context.controls))
    context.cooldowns = Mock()
    context.cooldowns.active.return_value = False
    context.in_api_cooldown = context.cooldowns.active
    context.record_api_error = context.cooldowns.record_error
    monkeypatch.setattr(bot, "_api_cooldown_owner", Mock(return_value=context.cooldowns))
    context.quote_schedule = Mock()
    context.schedule_next_quote_post = context.quote_schedule.schedule
    monkeypatch.setattr(bot, "_quote_schedule_owner", Mock(return_value=context.quote_schedule))
    context.meme_schedule = Mock()
    context.set_meme_delay_schedule = context.meme_schedule.set_delay
    monkeypatch.setattr(MainPostAssembly, "meme_schedule", lambda _assembly: context.meme_schedule)
    for name in (
        "lane_paused", "in_api_cooldown", "record_api_error",
        "schedule_next_quote_post", "set_meme_delay_schedule",
    ):
        monkeypatch.setattr(
            bot,
            name,
            Mock(side_effect=AssertionError(f"tick bounced through root {name}")),
        )
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "MEME_MIN_SECONDS_AFTER_QUOTE_POST", 90)
    monkeypatch.setattr(
        bot, "now_epoch", Mock(side_effect=AssertionError("use the tick epoch")),
    )
    return context


def run_lane(lane, tick):
    if lane == "quote":
        bot._run_due_quote_post_for_tick(
            tick.lines, tick.images, tick.state, tick.current,
        )
    else:
        bot._run_due_meme_post_for_tick(tick.state, tick.current)


def test_due_ticks_use_real_schedule_owners_without_root_relays(monkeypatch):
    current = 10_000
    saves = []
    monkeypatch.setattr(bot, "save_state", lambda state: saves.append(dict(state)))
    monkeypatch.setattr(bot, "POST_SLEEP_MIN", 600)
    monkeypatch.setattr(bot, "POST_SLEEP_MAX", 600)
    monkeypatch.setattr(bot.random, "randint", Mock(return_value=600))
    quote_schedule = bot._quote_schedule_owner()
    monkeypatch.setattr(bot, "_quote_schedule_owner", lambda: quote_schedule)
    controls = Mock()
    controls.lane_paused.return_value = False
    cooldowns = Mock()
    cooldowns.active.return_value = True
    monkeypatch.setattr(bot, "_runtime_controls_owner", lambda: controls)
    monkeypatch.setattr(bot, "_api_cooldown_owner", lambda: cooldowns)
    for name in ("schedule_next_quote_post", "set_meme_delay_schedule"):
        monkeypatch.setattr(
            bot,
            name,
            Mock(side_effect=AssertionError(f"due tick bounced through {name}")),
        )

    quote_state = {"next_quote_post_epoch": current}
    bot._run_due_quote_post_for_tick(set(), set(), quote_state, current)
    assert quote_state["next_quote_post_epoch"] == current + 600
    assert saves[-1]["next_quote_post_epoch"] == current + 600

    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    meme_schedule = bot._main_post_assembly().meme_schedule()
    monkeypatch.setattr(MainPostAssembly, "meme_schedule", lambda _assembly: meme_schedule)
    controls.lane_paused.return_value = True
    meme_state = {
        "next_meme_post_epoch": current,
        "last_quote_post_epoch": 0,
    }
    bot._run_due_meme_post_for_tick(meme_state, current)
    assert meme_state["next_meme_post_epoch"] == current + 300
    assert meme_state["next_meme_schedule_mode"] == "delayed_runtime_control"
    assert saves[-1]["next_meme_post_epoch"] == current + 300


@pytest.mark.parametrize("lane", ["quote", "meme"])
def test_future_posts_do_not_check_controls_or_write(lane, tick):
    tick.state[f"next_{lane}_post_epoch"] = tick.current + 1
    run_lane(lane, tick)
    tick.lane_paused.assert_not_called()
    tick.in_api_cooldown.assert_not_called()
    tick.post_random_quote.assert_not_called()
    tick.post_next_meme.assert_not_called()
    tick.save_state.assert_not_called()
    tick.schedule_next_quote_post.assert_not_called()
    tick.set_meme_delay_schedule.assert_not_called()


def test_disabled_meme_lane_does_not_read_schedule(monkeypatch, tick):
    class UnreadableState(dict):
        def get(self, *_args):
            pytest.fail("disabled meme lane must not inspect schedule state")

    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    tick.state = UnreadableState()
    run_lane("meme", tick)
    tick.lane_paused.assert_not_called()
    tick.post_next_meme.assert_not_called()


@pytest.mark.parametrize("lane", ["quote", "meme"])
def test_runtime_pause_takes_precedence_over_spacing_and_cooldown(lane, tick):
    tick.lane_paused.return_value = True
    tick.in_api_cooldown.return_value = True
    tick.state["last_quote_post_epoch"] = tick.current
    run_lane(lane, tick)
    tick.in_api_cooldown.assert_not_called()
    tick.post_random_quote.assert_not_called()
    tick.post_next_meme.assert_not_called()
    if lane == "quote":
        assert tick.state["next_quote_post_epoch"] == tick.current + 300
        tick.save_state.assert_called_once_with(tick.state)
        tick.schedule_next_quote_post.assert_not_called()
    else:
        tick.set_meme_delay_schedule.assert_called_once_with(
            tick.state, epoch=tick.current + 300, mode="delayed_runtime_control",
        )


def test_quote_write_cooldown_reschedules_from_tick_epoch(tick):
    tick.in_api_cooldown.return_value = True
    run_lane("quote", tick)
    tick.in_api_cooldown.assert_called_once_with(tick.state, scope="write")
    tick.post_random_quote.assert_not_called()
    tick.schedule_next_quote_post.assert_called_once_with(tick.state, tick.current)


@pytest.mark.parametrize(
    ("seconds_since_quote", "delay", "mode", "checks_cooldown"),
    [(89, 1800, "delayed_recent_quote", False),
     (90, 3600, "delayed_write_api_cooldown", True)],
)
def test_meme_spacing_boundary_and_cooldown_precedence(
    tick, seconds_since_quote, delay, mode, checks_cooldown,
):
    tick.state["last_quote_post_epoch"] = tick.current - seconds_since_quote
    tick.in_api_cooldown.return_value = True
    run_lane("meme", tick)
    assert tick.in_api_cooldown.called is checks_cooldown
    tick.post_next_meme.assert_not_called()
    tick.set_meme_delay_schedule.assert_called_once_with(
        tick.state, epoch=tick.current + delay, mode=mode,
    )


@pytest.mark.parametrize("lane", ["quote", "meme"])
def test_due_success_uses_original_objects_without_error_retry(lane, tick):
    run_lane(lane, tick)
    if lane == "quote":
        tick.post_random_quote.assert_called_once_with(
            tick.lines, tick.images, tick.state,
        )
        actual = tick.post_random_quote.call_args.args
        assert all(a is b for a, b in zip(actual, (tick.lines, tick.images, tick.state)))
    else:
        tick.post_next_meme.assert_called_once_with(tick.state)
        assert tick.post_next_meme.call_args.args[0] is tick.state
    tick.schedule_next_quote_post.assert_not_called()
    tick.set_meme_delay_schedule.assert_not_called()
    tick.record_api_error.assert_not_called()


@pytest.mark.parametrize("lane", ["quote", "meme"])
@pytest.mark.parametrize("failure_kind", ["ambiguous", "unrecoverable", "confirmed", "api", "unexpected", "preflight"])
def test_post_outcome_controls_retry_and_api_error_accounting(lane, failure_kind, tick):
    failures = {
        "ambiguous": bot.AmbiguousRemotePostOutcome("uncertain", service="x"),
        "unrecoverable": bot.UnrecoverableConfirmedPostPersistenceError("lost persistence"),
        "confirmed": bot.ConfirmedPostLocalPersistenceError("confirmed"),
        "api": bot.ApiError("request rejected", service="x", status_code=429),
        "unexpected": RuntimeError("local failure"),
        "preflight": bot.MediaUploadPreflightError("source image failed before publication"),
    }
    failure = failures[failure_kind]
    post = tick.post_random_quote if lane == "quote" else tick.post_next_meme
    post.side_effect = failure
    run_lane(lane, tick)
    post.assert_called_once()
    if failure_kind == "api":
        tick.record_api_error.assert_called_once_with(tick.state, failure, "x", scope="write")
    else:
        tick.record_api_error.assert_not_called()
    if failure_kind in {"ambiguous", "unrecoverable", "confirmed"}:
        tick.schedule_next_quote_post.assert_not_called()
        tick.set_meme_delay_schedule.assert_not_called()
    elif lane == "quote":
        tick.schedule_next_quote_post.assert_called_once_with(tick.state, tick.current)
        tick.set_meme_delay_schedule.assert_not_called()
    else:
        mode = "delayed_api_error" if failure_kind == "api" else "delayed_exception"
        tick.set_meme_delay_schedule.assert_called_once_with(
            tick.state, epoch=tick.current + 3600, mode=mode,
        )
        tick.schedule_next_quote_post.assert_not_called()


@pytest.mark.parametrize("lane", ["quote", "meme"])
def test_interruption_propagates_without_retry_or_api_accounting(lane, tick):
    failure = KeyboardInterrupt()
    post = tick.post_random_quote if lane == "quote" else tick.post_next_meme
    post.side_effect = failure
    with pytest.raises(KeyboardInterrupt) as caught:
        run_lane(lane, tick)
    assert caught.value is failure
    tick.record_api_error.assert_not_called()
    tick.schedule_next_quote_post.assert_not_called()
    tick.set_meme_delay_schedule.assert_not_called()


@pytest.mark.parametrize("lane", ["quote", "meme"])
def test_failed_api_bookkeeping_propagates_before_retry(lane, tick):
    post = tick.post_random_quote if lane == "quote" else tick.post_next_meme
    post.side_effect = bot.ApiError("rate limited", service="x")
    failure = OSError("state storage unavailable")
    tick.record_api_error.side_effect = failure
    with pytest.raises(OSError) as caught:
        run_lane(lane, tick)
    assert caught.value is failure
    tick.schedule_next_quote_post.assert_not_called()
    tick.set_meme_delay_schedule.assert_not_called()

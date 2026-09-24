"""Finite runtime ordering, arbitration and safety policy with isolated owners."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mrs_bot_tick_coordination import RuntimeCoordinator, RuntimeErrors, RuntimeSettings


def test_runtime_import_has_no_root_or_runtime_access():
    """Importing the coordinator must not acquire process authority."""
    code = """
import builtins, dataclasses, io, logging, os, random, socket, sys, time
from pathlib import Path
def forbidden(*args, **kwargs):
    raise AssertionError('runtime import attempted external access')
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'}:
        forbidden()
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.lstat = os.stat = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = logging.getLogger = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_tick_coordination
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


class Ambiguous(Exception):
    """An uncertain remote outcome in the fake runtime."""


class UnrecoverableReply(Exception):
    """A confirmed reply with failed local persistence."""


class UnrecoverablePost(Exception):
    """A confirmed main post with failed local persistence."""


class ConfirmedPost(Exception):
    """A confirmed post whose local completion failed."""


class ApiFailure(Exception):
    """A definite API failure in the fake runtime."""


def _runtime(*, paused=False, blocked=False, settings=None, initial_pause=False):
    """Bind explicit fake collaborators and a shared trace for one runtime."""
    events = []
    control = SimpleNamespace(global_paused=Mock(side_effect=lambda: events.append("pause") or paused),
                              lane_paused=Mock(return_value=False))
    cooldown = SimpleNamespace(active=Mock(return_value=False), record_error=Mock())
    quote_schedule = SimpleNamespace(schedule=Mock())
    meme_schedule = SimpleNamespace(set_delay=Mock())
    normal = Mock(side_effect=lambda state: events.append("normal") or "none")
    quote = Mock(side_effect=lambda state: events.append("quote_reply") or "none")
    post_quote = Mock(side_effect=lambda lines, images, state: events.append("quote_post"))
    post_meme = Mock(side_effect=lambda state: events.append("meme_post"))
    reply_instances = []
    main_instances = []

    def reply_assembly():
        assembly = SimpleNamespace(run_normal=normal, run_quote=quote)
        reply_instances.append(assembly)
        return assembly

    def main_assembly():
        assembly = SimpleNamespace(quote_runner=lambda: SimpleNamespace(post=post_quote),
                                   meme_runner=lambda: SimpleNamespace(post=post_meme),
                                   meme_schedule=lambda: meme_schedule)
        main_instances.append(assembly)
        return assembly

    def scheduler(state, key, *, current):
        events.append(("scheduler", key))
        return int(state.get(key, 0) or 0), False

    def save(state):
        events.append("save")

    def health(stage, **details):
        events.append(("health", stage, details))

    def historical(*, limit, runtime_state):
        events.append("historical")

    def recover(lines, images, state):
        events.append("recover")
        return {}

    def blocked_now():
        events.append("barrier")
        return blocked

    log = Mock()
    runtime = RuntimeCoordinator(
        settings=settings or RuntimeSettings(True, True, True, 0, 10, 10, 3, 90),
        errors=RuntimeErrors(Ambiguous, UnrecoverableReply, UnrecoverablePost,
                             ConfirmedPost, ApiFailure),
        controls=control, lane_controls=lambda: control, cooldowns=lambda: cooldown,
        quote_schedule=lambda: quote_schedule, reply_assembly=reply_assembly,
        main_post_assembly=main_assembly, now_epoch=lambda: events.append("clock") or 10_000,
        scheduler_epoch_from_state=scheduler, save_state=save, log=log,
        log_event=lambda *args, **kwargs: events.append(("event", args, kwargs)),
        report_health=health,
        resume_media_retirement=lambda: events.append("media_recovery"),
        resume_source_retirement=lambda **kwargs: events.append(("source_recovery", kwargs)),
        reconcile_confirmed_transactions=recover,
        ambiguous_remote_post_is_blocking=blocked_now,
        durable_remote_write_safety_barrier_exists=lambda: events.append("durable") or True,
        remote_write_safety_protocol_is_active=lambda: events.append("protocol") or True,
        process_historical_context=historical,
        maintenance_pause_logged=initial_pause,
    )
    return SimpleNamespace(runtime=runtime, events=events, control=control,
                           cooldown=cooldown, quote_schedule=quote_schedule,
                           meme_schedule=meme_schedule, normal=normal, quote=quote,
                           post_quote=post_quote, post_meme=post_meme,
                           reply_instances=reply_instances, main_instances=main_instances,
                           log=log)


def _state():
    return dict(last_reply_epoch=0, last_reply_check_epoch=0,
                last_quote_tweet_check_epoch=0, next_reply_lane_priority="normal",
                next_quote_post_epoch=0, next_meme_post_epoch=0,
                last_quote_post_epoch=0)


def test_complete_iteration_uses_original_objects_and_stage_order():
    tick = _runtime()
    state, lines, images = _state(), {"line"}, {"image"}
    assert tick.runtime.run_once(lines, images, state) == 60
    assert [event for event in tick.events if isinstance(event, str) and event != "save"] == [
        "pause", "media_recovery", "recover", "barrier", "clock", "historical",
        "barrier", "normal", "barrier", "quote_reply", "barrier", "barrier",
        "quote_post", "barrier", "meme_post",
    ]
    assert tick.events.index("historical") < tick.events.index("normal") < tick.events.index("quote_post") < tick.events.index("meme_post")
    assert tick.post_quote.call_args.args == (lines, images, state)
    assert all(actual is original for actual, original in zip(tick.post_quote.call_args.args, (lines, images, state)))
    assert tick.post_meme.call_args.args[0] is state
    assert tick.events[-1] == ("health", "sleep", {"loop_completed": True})


def test_paused_iterations_keep_allowed_recovery_and_initial_log_state():
    tick = _runtime(paused=True, initial_pause=True)
    state = _state()
    for _ in range(2):
        assert tick.runtime.run_once(set(), set(), state) == 60
    assert tick.events.count("pause") == 2
    assert tick.events.count("media_recovery") == tick.events.count("recover") == 0
    assert tick.events.count(("source_recovery", {"maintenance_paused": True})) == 2
    assert tick.events.count("durable") == 0
    assert tick.events.count("clock") == 0
    tick.log.warning.assert_not_called()
    tick.control.global_paused.side_effect = lambda: tick.events.append("pause") or False
    assert tick.runtime.run_once(set(), set(), state) == 60
    tick.log.info.assert_any_call("Global runtime control pause cleared; resuming scheduled lanes")


def test_blocked_iterations_recheck_durability_and_log_once_then_clear():
    tick = _runtime(blocked=True)
    state = _state()
    assert tick.runtime.run_once(set(), set(), state) == 60
    assert tick.runtime.run_once(set(), set(), state) == 60
    assert tick.events.count("durable") == 2
    assert tick.events.count("historical") == 0
    assert tick.log.critical.call_count == 1
    tick.runtime.ambiguous_remote_post_is_blocking = lambda: False
    assert tick.runtime.run_once(set(), set(), state) == 60
    assert tick.runtime.ambiguity_pause_logged is False
    tick.runtime.ambiguous_remote_post_is_blocking = lambda: True
    assert tick.runtime.run_once(set(), set(), state) == 60
    assert tick.log.critical.call_count == 2


@pytest.mark.parametrize("stage", ["historical", "normal", "quote_post"])
def test_new_barrier_returns_without_wait_or_later_stages(stage):
    tick = _runtime()
    state = _state()
    blocked = [False]
    tick.runtime.ambiguous_remote_post_is_blocking = lambda: blocked[0]
    if stage == "historical":
        tick.runtime.process_historical_context = lambda **kwargs: blocked.__setitem__(0, True)
    elif stage == "normal":
        tick.normal.side_effect = lambda state: blocked.__setitem__(0, True) or "none"
    else:
        tick.post_quote.side_effect = lambda *args: blocked.__setitem__(0, True)
    assert tick.runtime.run_once(set(), set(), state) is None
    if stage == "historical":
        tick.normal.assert_not_called()
    if stage in {"historical", "normal"}:
        tick.post_quote.assert_not_called()
    tick.post_meme.assert_not_called()
    assert tick.events[-1] == ("health", "remote_write_blocked",
                               {"remote_write_blocked": True, "loop_completed": True})


def test_due_work_reads_state_after_earlier_stages():
    tick = _runtime()
    state = _state()
    tick.post_quote.side_effect = lambda _lines, _images, current: current.__setitem__("last_quote_post_epoch", 10_000)
    assert tick.runtime.run_once(set(), set(), state) == 60
    tick.post_meme.assert_not_called()
    tick.meme_schedule.set_delay.assert_called_once_with(
        state, epoch=11_800, mode="delayed_recent_quote",
    )
    assert state["last_quote_post_epoch"] == 10_000
    tick.events.clear()
    state["next_quote_post_epoch"] = 20_000
    state["next_meme_post_epoch"] = 20_000
    tick.quote_schedule.schedule.reset_mock()
    tick.meme_schedule.set_delay.reset_mock()
    assert tick.runtime.run_once(set(), set(), state) == 60
    tick.quote_schedule.schedule.assert_not_called()
    tick.meme_schedule.set_delay.assert_not_called()


def test_forced_normal_and_distinct_spacing_retry_bookkeeping():
    tick = _runtime(settings=RuntimeSettings(True, True, False, 0, 20, 30, 7, 90))
    state = _state()
    state.update(last_reply_check_epoch=9_995, last_quote_tweet_check_epoch=0)
    tick.normal.side_effect = lambda state: tick.events.append("normal") or "skipped_spacing"
    tick.quote.side_effect = lambda state: tick.events.append("quote_reply") or "skipped_spacing"
    assert tick.runtime.run_reply_lane_checks_for_tick(state, 10_000) == (9_995, 9_977)
    assert state["last_reply_check_epoch"] == 9_995
    assert state["last_quote_tweet_check_epoch"] == 9_977
    assert tick.events.index("normal") < tick.events.index("quote_reply")
    assert tick.events.count("save") == 1


def test_posted_priority_save_and_quote_event_order():
    tick = _runtime()
    state = _state()
    state["next_reply_lane_priority"] = "quote"
    tick.quote.side_effect = lambda state: tick.events.append("quote_reply") or "posted"
    assert tick.runtime.run_reply_lane_checks_for_tick(state, 10_000) == (0, 10_000)
    assert state["next_reply_lane_priority"] == "normal"
    tick.normal.assert_not_called()
    names = [e if isinstance(e, str) else e[0] for e in tick.events]
    assert names.index("quote_reply") < names.index("event") < names.index("save")
    assert tick.events.count("save") == 2


def test_normal_post_saves_interval_before_priority_on_original_state():
    tick = _runtime()
    state = _state()
    snapshots = []
    tick.runtime.save_state = lambda current: snapshots.append((
        current is state, current["last_reply_check_epoch"],
        current["next_reply_lane_priority"],
    ))
    tick.normal.side_effect = lambda current: tick.events.append("normal") or "posted"
    assert tick.runtime.run_reply_lane_checks_for_tick(state, 10_000) == (10_000, 0)
    assert snapshots == [(True, 10_000, "normal"), (True, 10_000, "quote")]
    tick.quote.assert_not_called()


def test_recovery_failures_stay_bounded_but_stage_failures_propagate():
    tick = _runtime()
    state = _state()
    tick.runtime.resume_media_retirement = Mock(side_effect=OSError("media"))
    tick.runtime.resume_source_retirement = Mock(side_effect=OSError("source"))
    tick.runtime.reconcile_confirmed_transactions = Mock(side_effect=OSError("recovery"))
    assert tick.runtime.run_once(set(), set(), state) == 60
    assert tick.log.critical.call_count == 3
    tick.runtime.process_historical_context = Mock(side_effect=RuntimeError("historical"))
    with pytest.raises(RuntimeError, match="historical"):
        tick.runtime.run_once(set(), set(), state)
    tick.runtime.process_historical_context = lambda **kwargs: None
    state["last_reply_check_epoch"] = 0
    tick.normal.side_effect = RuntimeError("reply")
    with pytest.raises(RuntimeError, match="reply"):
        tick.runtime.run_once(set(), set(), state)


def test_fresh_assemblies_across_operations_and_ticks():
    tick = _runtime()
    state = _state()
    tick.runtime.run_once(set(), set(), state)
    state["last_reply_check_epoch"] = 0
    state["last_quote_tweet_check_epoch"] = 0
    tick.runtime.run_once(set(), set(), state)
    assert len(tick.reply_instances) == 4
    assert len({id(item) for item in tick.reply_instances}) == 4
    assert len(tick.main_instances) == 6  # meme schedule, quote and meme per tick
    assert len({id(item) for item in tick.main_instances}) == 6


def test_durability_failure_rechecked_and_sigint_propagates():
    tick = _runtime(blocked=True)
    tick.runtime.durable_remote_write_safety_barrier_exists = Mock(side_effect=OSError("marker"))
    assert tick.runtime.run_once(set(), set(), _state()) == 60
    assert tick.runtime.run_once(set(), set(), _state()) == 60
    assert tick.runtime.durable_remote_write_safety_barrier_exists.call_count == 2
    assert tick.log.critical.call_count == 3
    interrupt = KeyboardInterrupt("retained SIGINT")
    tick.runtime.durable_remote_write_safety_barrier_exists.side_effect = interrupt
    with pytest.raises(KeyboardInterrupt) as caught:
        tick.runtime.run_once(set(), set(), _state())
    assert caught.value is interrupt


def test_continuous_driver_skips_sleep_after_new_barrier_then_waits_when_blocked():
    tick = _runtime()
    state, lines, images = _state(), set(), set()
    blocked = [False]
    tick.runtime.ambiguous_remote_post_is_blocking = lambda: blocked[0]
    tick.runtime.process_historical_context = lambda **kwargs: blocked.__setitem__(0, True)
    waits = []

    class DriverStopped(Exception):
        """End the controlled driver after its first requested wait."""

    def sleeper(seconds):
        waits.append(seconds)
        raise DriverStopped

    with pytest.raises(DriverStopped):
        tick.runtime.run_continuously(lines, images, state, sleep=sleeper)
    assert waits == [60]
    assert tick.events.count("pause") == 2
    tick.normal.assert_not_called()

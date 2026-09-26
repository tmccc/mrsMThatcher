"""Regression tests for bot runtime safety."""

from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import exact_receipt_retirement as exact_retirement
from mrs_bot_daily_reply_accounting import DailyReplyAccounting
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    isolate_bot_runtime,
    configure_simple_quote_post,
    install_receipt_bound_x_request_stub,
    valid_regular_receipt,
)
from tests.helpers.reply_fixtures import (
    unit_confirmed_reply_receipt,
    unit_sending_reply_receipt,
    unit_sending_v4_reply_receipt,
)
from tests.test_media_upload_transaction_integration import (
    HARD_EXIT_CODES,
    _run_driver as run_media_driver,
)


pytestmark = pytest.mark.allow_loopback_network


def _patch_quote_runner(monkeypatch, operation):
    """Replace the main-post assembly's public quote application operation."""
    monkeypatch.setattr(bot._main_post_assembly_module.MainPostAssembly, "quote_runner",
                        lambda _assembly: SimpleNamespace(post=operation))


def _patch_meme_runner(monkeypatch, operation):
    """Replace the main-post assembly's public meme application operation."""
    monkeypatch.setattr(bot._main_post_assembly_module.MainPostAssembly, "meme_runner",
                        lambda _assembly: SimpleNamespace(post=operation))


def _patch_reply_lanes(monkeypatch, normal, quote):
    """Replace the reply assembly's public lane operations."""
    monkeypatch.setattr(bot._reply_assembly_module.ReplyAssembly, "run_normal",
                        lambda _assembly, state: normal(state))
    monkeypatch.setattr(bot._reply_assembly_module.ReplyAssembly, "run_quote",
                        lambda _assembly, state: quote(state))


def _confirmed_reply_waiting_for_startup(monkeypatch):
    """Leave a real confirmed receipt and owning journal after one fake send."""
    sending = unit_sending_v4_reply_receipt()
    remote_calls = []
    guard = object()

    def confirmed_remote(*_args, **_kwargs):
        remote_calls.append("confirmed")
        return {"data": {"id": "999"}}

    def stop_after_confirmation(actual_guard):
        assert actual_guard is guard
        raise KeyboardInterrupt("restart before local completion")

    install_receipt_bound_x_request_stub(monkeypatch, confirmed_remote)
    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", lambda: guard)
    monkeypatch.setattr(bot, "end_confirmed_post_sigint_deferral", stop_after_confirmation)
    with pytest.raises(KeyboardInterrupt, match="restart before local completion"):
        bot._reply_assembly().post_with_current_owners(
            state=bot.default_state(), receipt_template=sending,
            reply_text=str(sending["reply_text"]), reply_to_id=str(sending["target_id"]),
            made_with_ai=False, lane="mention",
        )
    status, receipt = bot.load_confirmed_reply_receipt()
    assert status == "valid" and receipt is not None
    journal = bot.journal_path_for_receipt(bot.CONFIRMED_REPLY_RECEIPT_FILE)
    assert bot.inspect_transport_state(journal).classification == "confirmed_pair"
    assert remote_calls == ["confirmed"]
    monkeypatch.setattr(bot, "x_request", lambda *_args, **_kwargs: pytest.fail(
        "startup recovery must not issue another remote request"))
    monkeypatch.setattr(bot, "create_post", lambda *_args, **_kwargs: pytest.fail(
        "startup recovery must not post again"))
    monkeypatch.setattr(bot._reply_generation.ReplyGeneration, "evaluate",
                        lambda *_args, **_kwargs: pytest.fail("startup must not reevaluate reply"))
    return receipt, journal, remote_calls


def _configure_confirmed_reply_startup(tmp_path, monkeypatch, receipt):
    """Keep main's real local-recovery flow and stop at runtime scheduling."""
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("A quote.\n", encoding="utf-8")
    state = bot.default_state()
    state["daily_reply_date"] = receipt["daily_reply_date"]
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "require_established_installation_after_ledger_recovery", lambda: None)
    monkeypatch.setattr(bot, "resume_interrupted_confirmed_media_retirement_if_present", lambda: False)
    monkeypatch.setattr(bot, "reconcile_runtime_historical_context_state", lambda: None)
    monkeypatch.setattr(bot, "_log_startup_configuration", lambda: None)
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_000)
    order = []

    class StartupReachedScheduler(Exception):
        pass

    monkeypatch.setattr(bot._tweet_lookup_cache.TweetLookupCache, "seed_recent_own_posts",
                        lambda _owner, _state: order.append("seed"))
    def stop_at_scheduler(_runtime, *_args, **_kwargs):
        raise StartupReachedScheduler

    monkeypatch.setattr(bot._tick_coordination.RuntimeCoordinator, "run_continuously",
                        stop_at_scheduler)
    return state, order, StartupReachedScheduler


@pytest.mark.parametrize("pause_between_passes", [False, True])
def test_startup_resumes_guard_prepared_before_transient_journal_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pause_between_passes: bool,
) -> None:
    receipt, journal, remote_calls = _confirmed_reply_waiting_for_startup(monkeypatch)
    state, order, StartupReachedScheduler = _configure_confirmed_reply_startup(
        tmp_path, monkeypatch, receipt,
    )
    source = bot.CONFIRMED_REPLY_RECEIPT_FILE
    original_retire = bot.retire_confirmed_transport_transaction
    original_source_recovery = bot.resume_source_receipt_retirement_for_control_snapshot
    original_reconcile = bot.reconcile_confirmed_transactions_before_global_barrier
    original_accounting = DailyReplyAccounting.record_confirmed
    applications = []
    injected = []
    pause = [False]
    guarded_snapshot = []

    def record_accounting(owner, *args, **kwargs):
        applications.append(kwargs.get("candidate_source"))
        return original_accounting(owner, *args, **kwargs)

    def retire_after_guard(**kwargs):
        prepared = exact_retirement.inspect_interrupted_receipt_retirement(source)
        assert prepared is not None and prepared.valid
        if not injected:
            injected.append("after_guard")
            raise OSError("one-off confirmed journal retirement failure")
        return original_retire(**kwargs)

    def source_recovery(*, maintenance_paused):
        order.append("source")
        if guarded_snapshot:
            assert not maintenance_paused
            assert any(os.path.lexists(path) for path in exact_retirement.retirement_auxiliary_paths(source))
        return original_source_recovery(maintenance_paused=maintenance_paused)

    def reconcile(*args):
        order.append("reconcile")
        return original_reconcile(*args)

    def sleep(seconds):
        assert seconds == 60
        order.append("sleep")
        if len(guarded_snapshot) == 0:
            guarded_snapshot.append(tuple(
                (path, path.read_bytes())
                for path in exact_retirement.retirement_barrier_paths(source)
                if os.path.lexists(path)
            ))
            assert bot.remote_receipt_retirement_is_blocking()
            assert bot.ambiguous_remote_post_is_blocking()
            assert not bot.remote_write_safety_protocol_is_active()
            assert not bot.remote_write_safety_incident_is_latched()
            pause[0] = pause_between_passes
        elif pause[0]:
            assert tuple(
                (path, path.read_bytes())
                for path in exact_retirement.retirement_barrier_paths(source)
                if os.path.lexists(path)
            ) == guarded_snapshot[0]
            assert order.count("source") == order.count("reconcile") == 1
            pause[0] = False
        else:
            pytest.fail("transient journal failure did not recover at the next pass")

    monkeypatch.setattr(DailyReplyAccounting, "record_confirmed", record_accounting)
    monkeypatch.setattr(bot, "retire_confirmed_transport_transaction", retire_after_guard)
    monkeypatch.setattr(bot, "resume_source_receipt_retirement_for_control_snapshot", source_recovery)
    monkeypatch.setattr(bot, "reconcile_confirmed_transactions_before_global_barrier", reconcile)
    monkeypatch.setattr(bot, "_runtime_controls_owner", lambda: SimpleNamespace(
        global_paused=lambda: pause[0]))
    monkeypatch.setattr(bot, "sleep", sleep)

    with pytest.raises(StartupReachedScheduler):
        bot.main()

    assert injected == ["after_guard"]
    assert order == (["source", "reconcile", "sleep"]
                     + (["sleep"] if pause_between_passes else [])
                     + ["source", "reconcile", "seed"])
    assert not any(os.path.lexists(path) for path in exact_retirement.retirement_barrier_paths(source))
    assert not os.path.lexists(journal)
    assert applications == ["mention"]
    assert state["daily_reply_count"] == 1
    assert state["own_auto_reply_ids"].count("999") == 1
    assert json.loads(bot.STATE_FILE.read_text())["daily_reply_count"] == 1
    assert remote_calls == ["confirmed"]
    assert not bot.remote_write_safety_incident_is_latched()


@pytest.mark.parametrize("fail_once", [False, True])
def test_startup_hands_unrelated_blocker_to_runtime_after_reply_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_once: bool,
) -> None:
    real_media_recovery = bot.resume_interrupted_confirmed_media_retirement_if_present
    receipt, journal, remote_calls = _confirmed_reply_waiting_for_startup(monkeypatch)
    state, order, _StartupReachedScheduler = _configure_confirmed_reply_startup(
        tmp_path, monkeypatch, receipt,
    )
    source = bot.CONFIRMED_REPLY_RECEIPT_FILE
    activation = bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE
    real_retire = bot.retire_confirmed_transport_transaction
    real_source_recovery = bot.resume_source_receipt_retirement_for_control_snapshot
    real_reconcile = bot.reconcile_confirmed_transactions_before_global_barrier
    real_accounting = DailyReplyAccounting.record_confirmed
    failures = []
    caught = []
    applications = []
    activation_removed = []

    def retire(**kwargs):
        prepared = exact_retirement.inspect_interrupted_receipt_retirement(source)
        assert prepared is not None and prepared.valid
        if fail_once and not failures:
            failures.append("journal")
            raise OSError("confirmed reply journal retirement failed once")
        return real_retire(**kwargs)

    def source_recovery(*, maintenance_paused):
        order.append("source")
        result = real_source_recovery(maintenance_paused=maintenance_paused)
        if fail_once and failures and not activation_removed:
            assert not source.exists() and not journal.exists()
            activation.unlink()  # Independent protocol loss after exact reply cleanup.
            activation_removed.append(True)
        return result

    def reconcile(*args):
        order.append("reconcile")
        try:
            result = real_reconcile(*args)
        except bot.ConfirmedReplyLocalPersistenceError as exc:
            caught.append(exc)
            raise
        if not fail_once and not activation_removed:
            assert not source.exists() and not journal.exists()
            activation.unlink()  # Independent protocol loss after exact reply cleanup.
            activation_removed.append(True)
        return result

    def record_accounting(owner, *args, **kwargs):
        applications.append(kwargs.get("candidate_source"))
        return real_accounting(owner, *args, **kwargs)

    def media_recovery():
        order.append("media")
        return real_media_recovery()

    def sleep(seconds):
        assert seconds == 60
        order.append("sleep")
        assert order.count("sleep") == 1 and fail_once, "startup failed to hand off"

    class RuntimeBlocked(Exception):
        pass

    def run_one_runtime_tick(runtime, lines_used, images_used, current_state, *, sleep):
        order.append("runtime")
        assert runtime.run_once(lines_used, images_used, current_state) == 60
        raise RuntimeBlocked

    def remote_action(*_args, **_kwargs):
        pytest.fail("unrelated blocker must prevent every remote lane")

    monkeypatch.setattr(bot, "retire_confirmed_transport_transaction", retire)
    monkeypatch.setattr(bot, "resume_source_receipt_retirement_for_control_snapshot",
                        source_recovery)
    monkeypatch.setattr(bot, "reconcile_confirmed_transactions_before_global_barrier", reconcile)
    monkeypatch.setattr(DailyReplyAccounting, "record_confirmed", record_accounting)
    monkeypatch.setattr(bot, "resume_interrupted_confirmed_media_retirement_if_present",
                        media_recovery)
    monkeypatch.setattr(bot, "sleep", sleep)
    monkeypatch.setattr(bot._tick_coordination.RuntimeCoordinator, "run_continuously",
                        run_one_runtime_tick)
    for name in ("upload_media", "post_random_quote", "post_next_meme",
                 "safely_process_due_historical_context_obligations"):
        monkeypatch.setattr(bot, name, remote_action)
    monkeypatch.setattr(bot._tick_coordination.RuntimeCoordinator,
                        "run_reply_lane_checks_for_tick", remote_action)

    with pytest.raises(RuntimeBlocked):
        bot.main()

    assert len(caught) == len(failures) == int(fail_once)
    assert activation_removed == [True]
    assert not activation.exists()
    assert not source.exists() and not journal.exists()
    assert order.count("sleep") == int(fail_once)
    assert order.count("media") == 2  # Startup prelude, then the runtime tick.
    assert order.index("runtime") > order.index("seed")
    assert order[order.index("runtime") + 1] == "media"
    assert bot.ambiguous_remote_post_is_blocking()
    assert applications == ["mention"]
    assert state["daily_reply_count"] == 1
    assert state["own_auto_reply_ids"].count("999") == 1
    assert json.loads(bot.STATE_FILE.read_text())["daily_reply_count"] == 1
    assert remote_calls == ["confirmed"]


def test_startup_handoff_runs_real_pending_media_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = run_media_driver(
        "during_media_retirement", lane="quote_image", state_directory=tmp_path,
    )
    assert prepared.returncode == HARD_EXIT_CODES["during_media_retirement"], (
        prepared.stdout, prepared.stderr,
    )
    media_path = tmp_path / bot.MEDIA_UPLOAD_RECEIPT_FILE.name
    monkeypatch.setattr(bot, "MEDIA_UPLOAD_RECEIPT_FILE", media_path)
    media_fence = bot.media_fence_path_for_receipt(media_path)
    main_receipt = bot.REGULAR_POST_RECEIPT_FILE
    main_journal = bot.journal_path_for_receipt(main_receipt)
    assert media_fence.exists() and main_receipt.exists() and main_journal.exists()
    assert bot.inspect_transport_state(main_journal).classification == "prepared_pair"

    real_media_recovery = bot.resume_interrupted_confirmed_media_retirement_if_present
    _state, order, _StartupReachedScheduler = _configure_confirmed_reply_startup(
        tmp_path, monkeypatch, unit_sending_v4_reply_receipt(),
    )
    calls = []

    def media_recovery():
        calls.append("media")
        if len(calls) == 1:
            raise OSError("one-off startup media-retirement failure")
        assert media_fence.exists()
        return real_media_recovery()

    class RuntimeBlocked(Exception):
        pass

    def run_one_runtime_tick(runtime, lines_used, images_used, state, *, sleep):
        order.append("runtime")
        assert runtime.run_once(lines_used, images_used, state) == 60
        raise RuntimeBlocked

    def remote_action(*_args, **_kwargs):
        pytest.fail("prepared main transaction must block every remote lane")

    monkeypatch.setattr(bot, "resume_interrupted_confirmed_media_retirement_if_present",
                        media_recovery)
    monkeypatch.setattr(bot._tick_coordination.RuntimeCoordinator, "run_continuously",
                        run_one_runtime_tick)
    monkeypatch.setattr(bot, "sleep", lambda _seconds: pytest.fail(
        "startup must hand media retirement to runtime"))
    for name in ("x_request", "create_post", "upload_media", "post_random_quote",
                 "post_next_meme", "safely_process_due_historical_context_obligations"):
        monkeypatch.setattr(bot, name, remote_action)
    monkeypatch.setattr(bot._tick_coordination.RuntimeCoordinator,
                        "run_reply_lane_checks_for_tick", remote_action)

    with pytest.raises(RuntimeBlocked):
        bot.main()

    assert calls == ["media", "media"]
    assert order.index("runtime") > order.index("seed")
    assert not media_fence.exists()
    assert main_receipt.exists() and main_journal.exists()
    assert bot.inspect_transport_state(main_journal).classification == "prepared_pair"
    assert bot.ambiguous_remote_post_is_blocking()


def test_startup_persistent_guarded_journal_failure_waits_with_incident_latch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt, journal, remote_calls = _confirmed_reply_waiting_for_startup(monkeypatch)
    state, order, _StartupReachedScheduler = _configure_confirmed_reply_startup(
        tmp_path, monkeypatch, receipt,
    )
    source = bot.CONFIRMED_REPLY_RECEIPT_FILE
    original_source_recovery = bot.resume_source_receipt_retirement_for_control_snapshot
    original_reconcile = bot.reconcile_confirmed_transactions_before_global_barrier
    retire_attempts = []

    def fail_after_guard(**_kwargs):
        prepared = exact_retirement.inspect_interrupted_receipt_retirement(source)
        assert prepared is not None and prepared.valid
        retire_attempts.append("journal")
        raise OSError("persistent confirmed journal retirement failure")

    def source_recovery(*, maintenance_paused):
        order.append("source")
        return original_source_recovery(maintenance_paused=maintenance_paused)

    def reconcile(*args):
        order.append("reconcile")
        return original_reconcile(*args)

    class StillBlocked(Exception):
        pass

    def sleep(seconds):
        assert seconds == 60
        order.append("sleep")
        assert source.exists() and journal.exists()
        if order.count("sleep") == 1:
            assert not bot.remote_write_safety_incident_is_latched()
        else:
            assert bot.remote_write_safety_incident_is_latched()
        if order.count("sleep") == 3:
            raise StillBlocked

    monkeypatch.setattr(bot, "retire_confirmed_transport_transaction", fail_after_guard)
    monkeypatch.setattr(bot, "resume_source_receipt_retirement_for_control_snapshot", source_recovery)
    monkeypatch.setattr(bot, "reconcile_confirmed_transactions_before_global_barrier", reconcile)
    monkeypatch.setattr(bot, "sleep", sleep)
    with pytest.raises(StillBlocked):
        bot.main()

    assert order == ["source", "reconcile", "sleep", "source", "sleep", "sleep"]
    assert retire_attempts == ["journal", "journal"]
    assert any(os.path.lexists(path) for path in exact_retirement.retirement_auxiliary_paths(source))
    assert bot.ambiguous_remote_post_is_blocking()
    assert state["daily_reply_count"] == 1
    assert remote_calls == ["confirmed"]


def test_startup_existing_incident_latch_does_not_start_local_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt, journal, remote_calls = _confirmed_reply_waiting_for_startup(monkeypatch)
    _state, order, _StartupReachedScheduler = _configure_confirmed_reply_startup(
        tmp_path, monkeypatch, receipt,
    )
    receipt_bytes = bot.CONFIRMED_REPLY_RECEIPT_FILE.read_bytes()
    journal_bytes = journal.read_bytes()
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", True)
    monkeypatch.setattr(bot, "resume_source_receipt_retirement_for_control_snapshot",
                        lambda **_kwargs: pytest.fail("incident latch must precede local retirement"))
    monkeypatch.setattr(bot, "reconcile_confirmed_transactions_before_global_barrier",
                        lambda *_args: pytest.fail("incident latch must precede reconciliation"))

    class IncidentStillBlocked(Exception):
        pass

    def sleep(seconds):
        assert seconds == 60
        order.append("sleep")
        raise IncidentStillBlocked

    monkeypatch.setattr(bot, "sleep", sleep)
    with pytest.raises(IncidentStillBlocked):
        bot.main()
    assert order == ["sleep"]
    assert bot.remote_write_safety_incident_is_latched()
    assert bot.CONFIRMED_REPLY_RECEIPT_FILE.read_bytes() == receipt_bytes
    assert journal.read_bytes() == journal_bytes
    assert remote_calls == ["confirmed"]


def test_startup_sending_reply_without_confirmed_lineage_remains_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_v4_reply_receipt()
    bot._reply_assembly().reply_receipts().write(sending, confirmed=False)
    _state, order, StartupReachedScheduler = _configure_confirmed_reply_startup(
        tmp_path, monkeypatch, sending,
    )
    receipt_bytes = bot.CONFIRMED_REPLY_RECEIPT_FILE.read_bytes()
    original_reconcile = bot.reconcile_confirmed_transactions_before_global_barrier
    outcomes = []

    def reconcile(*args):
        outcome = original_reconcile(*args)
        outcomes.append(outcome)
        return outcome

    monkeypatch.setattr(bot, "reconcile_confirmed_transactions_before_global_barrier",
                        reconcile)
    monkeypatch.setattr(bot, "x_request", lambda *_args, **_kwargs: pytest.fail(
        "ambiguous sending receipt must not make a remote request"))
    monkeypatch.setattr(bot, "create_post", lambda *_args, **_kwargs: pytest.fail(
        "ambiguous sending receipt must not be resent"))
    monkeypatch.setattr(bot._reply_generation.ReplyGeneration, "evaluate",
                        lambda *_args, **_kwargs: pytest.fail("ambiguous reply must not be reevaluated"))
    with pytest.raises(StartupReachedScheduler):
        bot.main()
    assert outcomes and outcomes[0]["conversational_reply"] is False
    assert bot.CONFIRMED_REPLY_RECEIPT_FILE.read_bytes() == receipt_bytes
    assert bot.ambiguous_remote_post_is_blocking()
    assert order == ["seed"]


def test_unrelated_startup_recovery_error_propagates_without_scheduling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt, journal, remote_calls = _confirmed_reply_waiting_for_startup(monkeypatch)
    _state, order, _StartupReachedScheduler = _configure_confirmed_reply_startup(
        tmp_path, monkeypatch, receipt,
    )
    original_bytes = bot.CONFIRMED_REPLY_RECEIPT_FILE.read_bytes()
    monkeypatch.setattr(bot, "resume_source_receipt_retirement_for_control_snapshot",
                        lambda **_kwargs: (_ for _ in ()).throw(
                            RuntimeError("unrelated startup recovery failure")))
    monkeypatch.setattr(bot, "sleep", lambda _seconds: pytest.fail(
        "unrelated recovery exception must propagate"))
    with pytest.raises(RuntimeError, match="unrelated startup recovery failure"):
        bot.main()
    assert order == []
    assert bot.CONFIRMED_REPLY_RECEIPT_FILE.read_bytes() == original_bytes
    assert journal.exists()
    assert remote_calls == ["confirmed"]


@pytest.mark.parametrize("recovers", [False, True])
def test_startup_retries_confirmed_reply_cleanup_before_state_mutation_or_scheduling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recovers: bool,
) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("A quote.\n", encoding="utf-8")
    state = bot.default_state()
    receipt = unit_confirmed_reply_receipt(epoch=2_000_000_000)
    state["daily_reply_date"] = receipt["daily_reply_date"]
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    bot._reply_assembly().reply_receipts().write(receipt, confirmed=True)
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "require_production_bootstrap", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "require_established_installation_after_ledger_recovery", lambda: None)
    monkeypatch.setattr(bot, "resume_interrupted_confirmed_media_retirement_if_present", lambda: False)
    monkeypatch.setattr(bot, "reconcile_runtime_historical_context_state", lambda: None)
    monkeypatch.setattr(bot, "_log_startup_configuration", lambda: None)
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "reconcile_startup_main_post_receipts", lambda *_args: None)
    monkeypatch.setattr(bot, "now_epoch", lambda: 10_000)
    order = []

    original_reconcile = bot.reconcile_confirmed_transactions_before_global_barrier
    original_retire = bot.retire_lane_transport_journal_if_present

    def retire(*args, **kwargs):
        if not recovers or order.count("reconcile") <= 2:
            raise OSError("confirmed receipt cleanup failed")
        return original_retire(*args, **kwargs)

    def reconcile(*args):
        order.append("reconcile")
        return original_reconcile(*args)

    def sleep(seconds):
        assert seconds == 60
        order.append("sleep")
        if not recovers and order.count("sleep") == 2:
            raise StartupBlocked

    class StartupComplete(Exception):
        pass

    class StartupBlocked(Exception):
        pass

    monkeypatch.setattr(bot, "reconcile_confirmed_transactions_before_global_barrier", reconcile)
    monkeypatch.setattr(bot, "retire_lane_transport_journal_if_present", retire)
    monkeypatch.setattr(bot, "sleep", sleep)
    monkeypatch.setattr(bot._tweet_lookup_cache.TweetLookupCache, "seed_recent_own_posts",
                        lambda _owner, _state: order.append("seed"))
    monkeypatch.setattr(bot, "_runtime_coordinator", lambda **_kwargs: SimpleNamespace(
        run_continuously=lambda *_args, **_kwargs: (_ for _ in ()).throw(StartupComplete)))

    with pytest.raises(StartupComplete if recovers else StartupBlocked):
        bot.main()
    if recovers:
        assert order[:5] == ["reconcile", "sleep", "reconcile", "sleep", "reconcile"]
        assert order.index("seed") > 4
        assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
        assert state["daily_reply_count"] == 1
    else:
        assert order == ["reconcile", "sleep", "reconcile", "sleep"]
        assert bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()


@pytest.mark.parametrize(
    "control_text",
    [
        json.dumps({"disable_all": True}),
        json.dumps({"pause_all": True}),
        "{",
    ],
)
def test_global_pause_leaves_startup_main_receipt_untouched(
    control_text: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        lines_used,
        images_used,
        state,
        _lines_used_file,
        _images_used_file,
        receipt_file,
        _lines_file,
    ) = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt = valid_regular_receipt(
        quote_post_epoch=1_784_680_936,
        next_quote_post_epoch=1_784_689_435,
    )
    bot.atomic_write_json(receipt_file, receipt)
    receipt_bytes = receipt_file.read_bytes()
    control_file = tmp_path / "mrsMThatcher.control.json"
    control_file.write_text(control_text, encoding="utf-8")
    monkeypatch.setattr(bot, "CONTROL_FILE", control_file)
    monkeypatch.setattr(
        bot,
        "_CONTROL_CACHE",
        {"signature": None, "data": {}, "has_valid": False, "failure_signature": None},
    )
    monkeypatch.setattr(
        bot._main_post_reconciliation,
        "reconcile_main_post_receipts",
        lambda *_args, **_kwargs: pytest.fail(
            "global maintenance pause must precede receipt reconciliation"
        ),
    )

    status = bot.reconcile_startup_main_post_receipts(
        lines_used,
        images_used,
        state,
        1_784_708_283,
    )

    assert status == {"regular": False, "meme": False}
    assert receipt_file.read_bytes() == receipt_bytes


def test_false_global_pause_preserves_startup_receipt_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_file = tmp_path / "mrsMThatcher.control.json"
    control_file.write_text(json.dumps({"disable_all": False}), encoding="utf-8")
    monkeypatch.setattr(bot, "CONTROL_FILE", control_file)
    monkeypatch.setattr(
        bot,
        "_CONTROL_CACHE",
        {"signature": None, "data": {}, "has_valid": False, "failure_signature": None},
    )
    expected = {"regular": True, "meme": False}
    monkeypatch.setattr(
        bot._main_post_reconciliation,
        "reconcile_main_post_receipts",
        lambda *_args, **_kwargs: expected,
    )
    monkeypatch.setattr(
        bot,
        "reconcile_main_post_receipts",
        lambda *_args, **_kwargs: pytest.fail(
            "startup reconciliation returned through root relay"
        ),
    )

    assert bot.reconcile_startup_main_post_receipts(
        set(),
        set(),
        {},
        1_784_708_283,
    ) is expected


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_fresh_startup_with_uncertain_main_attempt_idles_without_remote_action(
    lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "require_production_bootstrap", lambda: None)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "reconcile_runtime_historical_context_state", lambda: None)
    monkeypatch.setattr(bot, "glob", lambda _pattern: [])
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    state = {"next_quote_post_epoch": 1_800_007_200}
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot._tweet_lookup_cache.TweetLookupCache, "seed_recent_own_posts", lambda _owner, _state: None)
    monkeypatch.setattr(bot, "save_state", lambda _state, **_kwargs: None)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: False)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (
            int(state_arg.get(key, current) or current),
            False,
        ),
    )

    if lane == "quote_image":
        attempt = bot.build_main_post_attempt(
            lane=lane,
            text="Good quote.",
            media_ids=["media-1"],
            made_with_ai=False,
            selected_identity={
                "quote_hash": bot.quote_text_hash("Good quote."),
                "line_no": 0,
                "source_line_number": 1,
                "image_basename": "t01.jpg",
                "image_no": 0,
            },
            recovery_plan={
                "quote_delay_seconds": bot.POST_SLEEP_MIN,
                "meme_delay_seconds": None,
                "meme_scheduling_enabled": False,
                "meme_trigger_after_hour": int(bot.MEME_TRIGGER_AFTER_HOUR),
                "meme_schedule_version": int(bot.MEME_SCHEDULE_VERSION),
                "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
                "meme_schedule_before": bot.bound_meme_schedule_state(
                    {},
                    schedule_timezone=bot.MAIN_POST_SCHEDULE_TIMEZONE,
                ),
                "quote_history_after": [
                    bot.quote_text_hash("Good quote."),
                ],
                "image_history_after": ["t01.jpg"],
            },
        )
        receipt_path = bot.REGULAR_POST_RECEIPT_FILE
    else:
        attempt = bot.build_main_post_attempt(
            lane=lane,
            text=bot.MEME_POST_TEXT,
            media_ids=["media-1"],
            made_with_ai=False,
            selected_identity={"meme_basename": "001_meme.png"},
            recovery_plan={
                "next_schedule_mode": "fallback",
                "meme_schedule_version": int(bot.MEME_SCHEDULE_VERSION),
                "fallback_hour": int(bot.MEME_FALLBACK_HOUR),
                "fallback_minute": int(bot.MEME_FALLBACK_MINUTE),
                "image_summary": "Unit meme image.",
                "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
            },
        )
        receipt_path = bot.MEME_POST_RECEIPT_FILE
    attempting = {**attempt, "lifecycle_state": "attempting"}
    bot.atomic_write_json(receipt_path, attempting, durable=True)
    receipt_bytes = receipt_path.read_bytes()

    remote_action = tmp_path / "unexpected-remote-action"

    def forbidden_remote_action(*_args: object, **_kwargs: object) -> object:
        remote_action.write_text("reached", encoding="utf-8")
        raise AssertionError("startup ambiguity pause must precede remote lanes")

    for name in (
        "x_request",
        "upload_media",
        "create_post",
        "post_random_quote",
        "post_next_meme",
        "safely_process_due_historical_context_obligations",
    ):
        monkeypatch.setattr(bot, name, forbidden_remote_action)
    monkeypatch.setattr(bot, "sleep", lambda _seconds: os._exit(81))

    context = multiprocessing.get_context("fork")
    for _restart in range(2):
        process = context.Process(target=bot.main)
        process.start()
        process.join(timeout=10)
        assert process.exitcode == 81
        assert not remote_action.exists()
        assert receipt_path.read_bytes() == receipt_bytes


def test_main_global_pause_stops_before_every_remote_lane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    receipt_file = tmp_path / "regular_post_receipt.json"
    receipt_file.write_bytes(b'{"durable":"unchanged"}\n')
    receipt_bytes = receipt_file.read_bytes()
    control_file = tmp_path / "mrsMThatcher.control.json"
    control_file.write_text(json.dumps({"disable_all": True}), encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot, "CONTROL_FILE", control_file)
    monkeypatch.setattr(
        bot,
        "_CONTROL_CACHE",
        {"signature": None, "data": {}, "has_valid": False, "failure_signature": None},
    )
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "glob", lambda _pattern: [])
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    state = {"next_quote_post_epoch": 0}
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot._tweet_lookup_cache.TweetLookupCache, "seed_recent_own_posts", lambda _owner, _state: None)
    monkeypatch.setattr(bot, "save_state", lambda _state, **_kwargs: None)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_784_708_283)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda _state, _key, current: (current, False),
    )

    def remote_lane_reached(*_args, **_kwargs):
        pytest.fail("global maintenance pause must block every remote lane")

    monkeypatch.setattr(bot, "reconcile_main_post_receipts", remote_lane_reached)
    monkeypatch.setattr(bot._tick_coordination.RuntimeCoordinator, "run_reply_lane_checks_for_tick",
                        lambda _runtime, state, current: remote_lane_reached(state, current))
    monkeypatch.setattr(bot, "post_random_quote", remote_lane_reached)
    monkeypatch.setattr(bot, "post_next_meme", remote_lane_reached)
    monkeypatch.setattr(bot, "create_post", remote_lane_reached)
    monkeypatch.setattr(bot, "upload_media", remote_lane_reached)
    monkeypatch.setattr(bot, "x_request", remote_lane_reached)
    monkeypatch.setattr(
        bot._reply_model_transport.ReplyModelTransport, "call",
        lambda _owner, *args, **kwargs: remote_lane_reached(*args, **kwargs),
    )

    class MaintenanceTickComplete(Exception):
        pass

    monkeypatch.setattr(
        bot,
        "sleep",
        lambda seconds: (_ for _ in ()).throw(MaintenanceTickComplete),
    )

    with pytest.raises(MaintenanceTickComplete):
        bot.main()

    assert receipt_file.read_bytes() == receipt_bytes


def test_main_total_persistence_loss_latch_stops_later_remote_lanes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "reconcile_runtime_historical_context_state", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "glob", lambda _pattern: [])
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot._daily_meme.MemeCatalog, "candidates", lambda _owner: [])
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    state = {
        "next_quote_post_epoch": 1,
        "next_meme_post_epoch": 1,
        "last_quote_post_epoch": 0,
    }
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "reconcile_startup_main_post_receipts", lambda *_args: None)
    monkeypatch.setattr(bot._tweet_lookup_cache.TweetLookupCache, "seed_recent_own_posts", lambda _owner, _state: None)
    monkeypatch.setattr(bot, "save_state", lambda _state, **_kwargs: None)
    monkeypatch.setattr(
        bot._daily_meme.MemeSchedule,
        "ensure_initialized",
        lambda _owner, _state: None,
    )
    clock_must_not_run = False

    def controlled_clock() -> int:
        if clock_must_not_run:
            pytest.fail("the safety latch must be checked before the next clock read")
        return 1_784_708_283

    monkeypatch.setattr(bot, "now_epoch", controlled_clock)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (int(state_arg.get(key, current)), False),
    )

    context_ticks = 0
    reply_ticks = 0
    quote_attempts = 0

    def context_tick(*_args: object, **_kwargs: object) -> list[dict]:
        nonlocal context_ticks
        context_ticks += 1
        return []

    def reply_tick(
        _state: dict,
        _current: int,
    ) -> tuple[int, int]:
        nonlocal reply_ticks
        reply_ticks += 1
        return 0, 0

    def catastrophic_quote(*_args: object, **_kwargs: object) -> None:
        nonlocal clock_must_not_run, quote_attempts
        quote_attempts += 1
        bot.latch_confirmed_post_persistence_failure(
            lane="quote_image",
            post_id="950001",
            failure_components=["regular_post_receipt", "state"],
        )
        clock_must_not_run = True
        raise bot.UnrecoverableConfirmedPostPersistenceError("confirmed and unrepresented")

    monkeypatch.setattr(bot, "safely_process_due_historical_context_obligations", context_tick)
    monkeypatch.setattr(bot._tick_coordination.RuntimeCoordinator, "run_reply_lane_checks_for_tick",
                        lambda _runtime, state, current: reply_tick(state, current))
    _patch_quote_runner(monkeypatch, catastrophic_quote)
    monkeypatch.setattr(
        bot,
        "schedule_next_quote_post",
        lambda *_args, **_kwargs: pytest.fail(
            "an unrecoverable confirmed post must never be scheduled for retry"
        ),
    )
    _patch_meme_runner(monkeypatch, lambda _state: pytest.fail(
        "meme lane must not run after the safety latch"))
    monkeypatch.setattr(
        bot,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("marker failed")),
    )

    class SafetyBarrierTickComplete(Exception):
        pass

    monkeypatch.setattr(
        bot,
        "sleep",
        lambda seconds: (_ for _ in ()).throw(SafetyBarrierTickComplete),
    )

    with pytest.raises(SafetyBarrierTickComplete):
        bot.main()

    assert quote_attempts == 1
    assert context_ticks == 1
    assert reply_ticks == 1
    assert bot.ambiguous_remote_post_is_blocking() is True
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()


@pytest.mark.parametrize(
    ("lane", "failure_kind"),
    [
        ("quote", "ambiguous"),
        ("meme", "ambiguous"),
        ("meme", "unrecoverable"),
    ],
)
def test_main_routes_remote_safety_failures_without_error_retry_bookkeeping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
    failure_kind: str,
) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "reconcile_runtime_historical_context_state", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "glob", lambda _pattern: [])
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot._daily_meme.MemeCatalog, "candidates", lambda _owner: [])
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    current = 1_784_708_283
    state = {
        "next_quote_post_epoch": 1 if lane == "quote" else current + 3600,
        "next_meme_post_epoch": 1,
        "last_quote_post_epoch": 0,
    }
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "reconcile_startup_main_post_receipts", lambda *_args: None)
    monkeypatch.setattr(bot._tweet_lookup_cache.TweetLookupCache, "seed_recent_own_posts", lambda _owner, _state: None)
    monkeypatch.setattr(bot, "save_state", lambda _state, **_kwargs: None)
    monkeypatch.setattr(
        bot._daily_meme.MemeSchedule,
        "ensure_initialized",
        lambda _owner, _state: None,
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: current)
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: False)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (int(state_arg.get(key, current)), False),
    )
    monkeypatch.setattr(
        bot,
        "safely_process_due_historical_context_obligations",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        bot._tick_coordination.RuntimeCoordinator,
        "run_reply_lane_checks_for_tick",
        lambda _runtime, _state, _current: (0, 0),
    )
    monkeypatch.setattr(
        bot._api_cooldowns.ApiCooldowns,
        "record_error",
        lambda *_args, **_kwargs: pytest.fail(
            "remote safety exceptions must not enter API-error bookkeeping"
        ),
    )
    monkeypatch.setattr(
        bot._runtime_state_helpers.QuoteSchedule,
        "schedule",
        lambda *_args, **_kwargs: pytest.fail(
            "remote safety exceptions must not schedule a quote retry"
        ),
    )
    monkeypatch.setattr(
        bot._daily_meme.MemeSchedule,
        "set_delay",
        lambda *_args, **_kwargs: pytest.fail(
            "remote safety exceptions must not schedule a meme retry"
        ),
    )
    monkeypatch.setattr(
        bot,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("marker failed")),
    )

    def raise_safety_failure() -> None:
        if failure_kind == "ambiguous":
            bot.record_ambiguous_remote_post({"text": "confirmed or ambiguous"})
            raise bot.AmbiguousRemotePostOutcome("ambiguous", service="x")
        bot.latch_confirmed_post_persistence_failure(
            lane="daily_meme",
            post_id="970001",
            failure_components=["meme_post_receipt", "state"],
        )
        raise bot.UnrecoverableConfirmedPostPersistenceError(
            "confirmed and unrepresented"
        )

    if lane == "quote":
        _patch_quote_runner(monkeypatch, lambda *_args: raise_safety_failure())
        _patch_meme_runner(monkeypatch, lambda _state: pytest.fail(
            "meme lane must not run through the safety latch"))
    else:
        _patch_quote_runner(monkeypatch, lambda *_args: pytest.fail("future quote lane must not run"))
        _patch_meme_runner(monkeypatch, lambda _state: raise_safety_failure())

    class SafetyBarrierTickComplete(Exception):
        pass

    monkeypatch.setattr(
        bot,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(SafetyBarrierTickComplete),
    )

    with pytest.raises(SafetyBarrierTickComplete):
        bot.main()

    assert bot.ambiguous_remote_post_is_blocking() is True
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()


def test_confirmed_persistence_marker_allows_controlled_startup_before_idle_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot.latch_confirmed_post_persistence_failure(
        lane="quote_image",
        post_id="950001",
        failure_components=["regular_post_receipt", "state"],
    )
    assert bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    order: list[str] = []
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: order.append("lock"))

    class StartupReachedBarrier(RuntimeError):
        pass

    def stop_after_startup_reconciliation() -> None:
        order.append("context_reconcile")
        raise StartupReachedBarrier

    monkeypatch.setattr(
        bot,
        "reconcile_runtime_historical_context_state",
        stop_after_startup_reconciliation,
    )

    with pytest.raises(StartupReachedBarrier):
        bot.main()
    assert order == ["lock", "context_reconcile"]
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="remote-write safety barrier",
    ):
        bot.block_if_ambiguous_remote_post()


def test_test_post_quote_reports_confirmed_local_failure_distinctly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: {})
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda images: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(
        bot,
        "post_random_quote",
        lambda *args, **kwargs: (_ for _ in ()).throw(bot.ConfirmedPostLocalPersistenceError("confirmed")),
    )
    saved: list[dict] = []
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: saved.append(dict(state)))

    assert bot.run_test_post_quote() == 3
    assert saved == [{}]


def test_one_shot_quote_unrecoverable_failure_cannot_exit_on_memory_latch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: {})
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _images: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(
        bot,
        "post_random_quote",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            bot.UnrecoverableConfirmedPostPersistenceError("confirmed")
        ),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: pytest.fail(
            "unrecoverable confirmed post must not use ordinary exit bookkeeping"
        ),
    )
    lanes: list[str] = []

    class ProcessHeld(Exception):
        pass

    def hold(*, lane: str) -> None:
        lanes.append(lane)
        raise ProcessHeld

    monkeypatch.setattr(bot, "wait_for_durable_barrier_before_one_shot_exit", hold)

    with pytest.raises(ProcessHeld):
        bot.run_test_post_quote()

    assert lanes == ["quote_image"]


def test_test_post_quote_migrates_old_meme_schedule_before_post_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    state = {
        "meme_schedule_version": 1,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    seen_states: list[dict] = []
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda images: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "post_random_quote", lambda lines_used, images_used, state: seen_states.append(json.loads(json.dumps(state))))

    assert bot.run_test_post_quote() == 0
    assert seen_states[0]["meme_schedule_version"] == bot.MEME_SCHEDULE_VERSION
    assert seen_states[0]["next_meme_schedule_mode"] == "fallback_migrated"


def test_test_post_quote_preserves_current_meme_schedule_preflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    state = {
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    before = json.loads(json.dumps(state))
    seen_states: list[dict] = []
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda images: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "post_random_quote", lambda lines_used, images_used, state: seen_states.append(json.loads(json.dumps(state))))

    assert bot.run_test_post_quote() == 0
    assert seen_states[0] == before


def test_test_post_quote_load_failure_happens_before_post_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: (_ for _ in ()).throw(RuntimeError("future meme schedule version")))
    monkeypatch.setattr(bot, "post_random_quote", lambda *args, **kwargs: pytest.fail("post_random_quote should not be called"))

    with pytest.raises(RuntimeError, match="future meme schedule version"):
        bot.run_test_post_quote()


def test_test_post_quote_receipt_replay_does_not_create_second_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.atomic_write_json(receipt_file, valid_regular_receipt())
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda lines: lines_used)
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda images: images_used)
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called after receipt replay"))

    assert bot.run_test_post_quote() == 0
    assert not receipt_file.exists()
    assert state["last_main_post_id"] == "950001"


def test_test_post_meme_reports_confirmed_local_failure_distinctly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: {})
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        bot,
        "post_next_meme",
        lambda *args, **kwargs: (_ for _ in ()).throw(bot.ConfirmedPostLocalPersistenceError("confirmed")),
    )
    saved: list[dict] = []
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: saved.append(dict(state)))

    assert bot.run_test_post_meme() == 3
    assert saved == [{}]


def test_one_shot_meme_ambiguity_cannot_exit_on_memory_latch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: {})
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        bot,
        "post_next_meme",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            bot.AmbiguousRemotePostOutcome("ambiguous", service="x")
        ),
    )
    monkeypatch.setattr(
        bot,
        "record_api_error",
        lambda *_args, **_kwargs: pytest.fail(
            "ambiguous outcome must not enter ordinary API-error bookkeeping"
        ),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: pytest.fail(
            "ambiguous outcome must not use ordinary exit bookkeeping"
        ),
    )
    lanes: list[str] = []

    class ProcessHeld(Exception):
        pass

    def hold(*, lane: str) -> None:
        lanes.append(lane)
        raise ProcessHeld

    monkeypatch.setattr(bot, "wait_for_durable_barrier_before_one_shot_exit", hold)

    with pytest.raises(ProcessHeld):
        bot.run_test_post_meme()

    assert lanes == ["daily_meme"]


def test_one_shot_memory_only_barrier_waits_for_durable_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", True)

    class ProcessHeld(Exception):
        pass

    monkeypatch.setattr(
        bot,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(ProcessHeld),
    )

    with pytest.raises(ProcessHeld):
        bot.wait_for_durable_barrier_before_one_shot_exit(lane="quote_image")


def test_test_main_tick_stops_after_reply_safety_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    sending = unit_sending_reply_receipt()
    waited: list[str] = []

    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_000)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (int(state_arg.get(key, 0) or 0), False),
    )

    def trigger_reply_barrier(*_args: object, **_kwargs: object) -> tuple[int, int]:
        bot._reply_assembly().reply_receipts().write(sending, confirmed=False)
        return 0, 0

    monkeypatch.setattr(bot._tick_coordination.RuntimeCoordinator, "run_reply_lane_checks_for_tick",
                        lambda _runtime, state, current: trigger_reply_barrier(state, current))
    monkeypatch.setattr(
        bot,
        "wait_for_durable_barrier_before_one_shot_exit",
        lambda *, lane: waited.append(lane),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: pytest.fail(
            "one-shot tick must not perform ordinary completion save after barrier"
        ),
    )

    assert bot.run_test_main_tick() == 3
    assert waited == ["production_reply_tick"]
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)


@pytest.mark.parametrize(
    ("priority", "first_lane", "failure_type"),
    [
        ("normal", "normal", bot.AmbiguousRemotePostOutcome),
        (
            "normal",
            "normal",
            bot.UnrecoverableConfirmedReplyPersistenceError,
        ),
        ("quote", "quote_tweet", bot.AmbiguousRemotePostOutcome),
        (
            "quote",
            "quote_tweet",
            bot.UnrecoverableConfirmedReplyPersistenceError,
        ),
    ],
)
def test_production_reply_tick_stops_sibling_lane_on_safety_failure(
    monkeypatch: pytest.MonkeyPatch,
    priority: str,
    first_lane: str,
    failure_type: type[BaseException],
) -> None:
    state = bot.default_state()
    state.update(
        {
            "next_reply_lane_priority": priority,
            "last_reply_epoch": 0,
            "last_reply_check_epoch": 0,
            "last_quote_tweet_check_epoch": 0,
        }
    )
    sending = unit_sending_reply_receipt(
        lane="quote_tweet" if first_lane == "quote_tweet" else "mention",
    )
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "REPLY_CHECK_EVERY_SECONDS", 1)
    monkeypatch.setattr(bot, "QUOTE_CHECK_EVERY_SECONDS", 1)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (int(state_arg.get(key, 0) or 0), False),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: pytest.fail(
            "reply safety failure must not consume or save a scheduler interval"
        ),
    )

    def safety_failure(_state: dict) -> str:
        bot._reply_assembly().reply_receipts().write(sending, confirmed=False)
        if issubclass(failure_type, bot.ApiError):
            raise failure_type("reply safety failure", service="x")
        raise failure_type("reply safety failure")

    def later_lane(_state: dict) -> str:
        pytest.fail("the sibling reply lane must not run after a safety failure")

    _patch_reply_lanes(
        monkeypatch,
        safety_failure if first_lane == "normal" else later_lane,
        safety_failure if first_lane == "quote_tweet" else later_lane,
    )

    assert bot._runtime_coordinator().run_reply_lane_checks_for_tick(state, 100) == (0, 0)
    assert bot.ambiguous_remote_post_is_blocking() is True
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)


@pytest.mark.parametrize(
    ("priority", "first_lane", "failure_type"),
    [
        ("normal", "normal", bot.AmbiguousRemotePostOutcome),
        (
            "normal",
            "normal",
            bot.UnrecoverableConfirmedReplyPersistenceError,
        ),
        ("quote", "quote_tweet", bot.AmbiguousRemotePostOutcome),
        (
            "quote",
            "quote_tweet",
            bot.UnrecoverableConfirmedReplyPersistenceError,
        ),
    ],
)
def test_main_reply_safety_failure_reaches_top_of_loop_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    priority: str,
    first_lane: str,
    failure_type: type[BaseException],
) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    current = 1_784_708_283
    state = bot.default_state()
    state.update(
        {
            "next_reply_lane_priority": priority,
            "last_reply_epoch": 0,
            "last_reply_check_epoch": 0,
            "last_quote_tweet_check_epoch": 0,
            "next_quote_post_epoch": 0,
            "next_meme_post_epoch": 0,
            "last_quote_post_epoch": 0,
        }
    )
    sending = unit_sending_reply_receipt(
        lane="quote_tweet" if first_lane == "quote_tweet" else "mention",
    )
    clock_must_not_run = False
    context_ticks = 0
    reply_attempts = 0

    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "reconcile_runtime_historical_context_state", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "glob", lambda _pattern: [])
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "REPLY_CHECK_EVERY_SECONDS", 1)
    monkeypatch.setattr(bot, "QUOTE_CHECK_EVERY_SECONDS", 1)
    monkeypatch.setattr(bot._daily_meme.MemeCatalog, "candidates", lambda _owner: [])
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "reconcile_startup_main_post_receipts", lambda *_args: None)
    monkeypatch.setattr(bot._tweet_lookup_cache.TweetLookupCache, "seed_recent_own_posts", lambda _owner, _state: None)
    monkeypatch.setattr(bot, "save_state", lambda _state, **_kwargs: None)
    monkeypatch.setattr(
        bot._daily_meme.MemeSchedule,
        "ensure_initialized",
        lambda _owner, _state: None,
    )
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: False)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (int(state_arg.get(key, 0) or 0), False),
    )

    def controlled_clock() -> int:
        if clock_must_not_run:
            pytest.fail("the top-of-loop barrier must run before another clock read")
        return current

    def context_tick(*_args: object, **_kwargs: object) -> list[dict]:
        nonlocal context_ticks
        context_ticks += 1
        return []

    def safety_failure(_state: dict) -> str:
        nonlocal clock_must_not_run, reply_attempts
        reply_attempts += 1
        bot._reply_assembly().reply_receipts().write(sending, confirmed=False)
        clock_must_not_run = True
        if issubclass(failure_type, bot.ApiError):
            raise failure_type("reply safety failure", service="x")
        raise failure_type("reply safety failure")

    def later_lane(_state: dict) -> str:
        pytest.fail("the sibling reply lane must not run after a safety failure")

    monkeypatch.setattr(bot, "now_epoch", controlled_clock)
    monkeypatch.setattr(
        bot,
        "safely_process_due_historical_context_obligations",
        context_tick,
    )
    _patch_reply_lanes(
        monkeypatch,
        safety_failure if first_lane == "normal" else later_lane,
        safety_failure if first_lane == "quote_tweet" else later_lane,
    )
    _patch_quote_runner(monkeypatch, lambda *_args: pytest.fail(
        "main quote lane must not run after a reply safety failure"))
    _patch_meme_runner(monkeypatch, lambda _state: pytest.fail(
        "meme lane must not run after a reply safety failure"))

    class SafetyBarrierTickComplete(Exception):
        pass

    monkeypatch.setattr(
        bot,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(SafetyBarrierTickComplete),
    )

    with pytest.raises(SafetyBarrierTickComplete):
        bot.main()

    assert reply_attempts == 1
    assert context_ticks == 1
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)
    assert bot.ambiguous_remote_post_is_blocking() is True


@pytest.mark.parametrize(
    ("priority", "first_lane", "failure_type", "expected_wait_lane"),
    [
        (
            "normal",
            "normal",
            bot.AmbiguousRemotePostOutcome,
            "normal_reply",
        ),
        (
            "normal",
            "normal",
            bot.UnrecoverableConfirmedReplyPersistenceError,
            "normal_reply",
        ),
        (
            "quote",
            "quote_tweet",
            bot.AmbiguousRemotePostOutcome,
            "quote_tweet_reply",
        ),
        (
            "quote",
            "quote_tweet",
            bot.UnrecoverableConfirmedReplyPersistenceError,
            "quote_tweet_reply",
        ),
    ],
)
def test_test_cycle_reply_safety_failure_stops_later_lane(
    monkeypatch: pytest.MonkeyPatch,
    priority: str,
    first_lane: str,
    failure_type: type[BaseException],
    expected_wait_lane: str,
) -> None:
    state = bot.default_state()
    state["next_reply_lane_priority"] = priority
    sending = unit_sending_reply_receipt(
        lane="quote_tweet" if first_lane == "quote_tweet" else "mention",
    )
    waited: list[str] = []

    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: pytest.fail(
            "test cycle must not perform a final save after reply safety failure"
        ),
    )
    monkeypatch.setattr(
        bot,
        "wait_for_durable_barrier_before_one_shot_exit",
        lambda *, lane: waited.append(lane),
    )

    def safety_failure(_state: dict) -> str:
        bot._reply_assembly().reply_receipts().write(sending, confirmed=False)
        if issubclass(failure_type, bot.ApiError):
            raise failure_type("reply safety failure", service="x")
        raise failure_type("reply safety failure")

    def later_lane(_state: dict) -> str:
        pytest.fail("the sibling reply lane must not run after a safety failure")

    _patch_reply_lanes(
        monkeypatch,
        safety_failure if first_lane == "normal" else later_lane,
        safety_failure if first_lane == "quote_tweet" else later_lane,
    )

    assert bot.run_test_cycle() == 3
    assert waited == [expected_wait_lane]
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)


def test_test_post_meme_migrates_old_meme_schedule_before_post_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    state = {
        "meme_schedule_version": 1,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    seen_states: list[dict] = []
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "post_next_meme", lambda state: seen_states.append(json.loads(json.dumps(state))))

    assert bot.run_test_post_meme() == 0
    assert seen_states[0]["meme_schedule_version"] == bot.MEME_SCHEDULE_VERSION
    assert seen_states[0]["next_meme_schedule_mode"] == "fallback_migrated"

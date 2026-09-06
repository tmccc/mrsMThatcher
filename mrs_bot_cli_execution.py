"""Command-line dispatch and one-shot execution.

The root supplies current runtime dependencies explicitly on each call. This
module performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

from typing import Any


def run_test_cycle(
    *,
    AmbiguousRemotePostOutcome: Any,
    BASE_DIR: Any,
    LOG_FILE: Any,
    OPENAI_BASE: Any,
    STATE_FILE: Any,
    UnrecoverableConfirmedReplyPersistenceError: Any,
    X_BASE: Any,
    X_UPLOAD_BASE: Any,
    acquire_instance_lock: Any,
    ambiguous_remote_post_is_blocking: Any,
    block_if_ambiguous_remote_post: Any,
    load_runtime_state: Any,
    log: Any,
    log_event: Any,
    maybe_reply_to_mentions: Any,
    maybe_reply_to_quote_tweets: Any,
    reconcile_runtime_historical_context_state: Any,
    require_established_installation_after_ledger_recovery: Any,
    require_production_bootstrap: Any,
    require_test_mode: Any,
    save_state: Any,
    wait_for_durable_barrier_before_one_shot_exit: Any,
) -> int:
    """Run one local integration-test pass without entering the posting loop."""
    if not require_test_mode("--test-cycle"):
        return 2
    require_production_bootstrap()

    acquire_instance_lock()
    require_established_installation_after_ledger_recovery()
    reconcile_runtime_historical_context_state()
    block_if_ambiguous_remote_post()

    log.info("Running one test cycle")
    log.info("Base dir=%s", BASE_DIR)
    log.info("State file=%s", STATE_FILE)
    log.info("Log file=%s", LOG_FILE)
    log.info("X base=%s", X_BASE)
    log.info("X upload base=%s", X_UPLOAD_BASE)
    log.info("OpenAI base=%s", OPENAI_BASE)

    state = load_runtime_state()

    reply_lane_priority = str(state.get("next_reply_lane_priority", "normal") or "normal")

    quote_status = None
    before_reply_epoch = int(state.get("last_reply_epoch", 0) or 0)

    def finish_if_ambiguity_blocked() -> bool:
        if not ambiguous_remote_post_is_blocking():
            return False
        log.critical("Test cycle stopped after an ambiguous remote post; no later lane will run")
        save_state(state)
        return True

    def run_test_reply_action(lane: str, action) -> tuple[object | None, bool]:
        try:
            return action(state), False
        except (
            AmbiguousRemotePostOutcome,
            UnrecoverableConfirmedReplyPersistenceError,
        ):
            log.critical(
                "Test-cycle %s reply lane stopped by the global remote-write "
                "safety barrier",
                lane,
                exc_info=True,
            )
            wait_for_durable_barrier_before_one_shot_exit(
                lane=f"{lane}_reply",
            )
            return None, True

    if reply_lane_priority == "quote":
        quote_status, safety_stopped = run_test_reply_action(
            "quote_tweet",
            maybe_reply_to_quote_tweets,
        )
        if safety_stopped:
            return 0
        if finish_if_ambiguity_blocked():
            return 0
        log.info("Test-cycle quote-tweet check status=%s", quote_status)
        log_event("quote_check_status", status=quote_status, priority="test_cycle")
        after_quote_epoch = int(state.get("last_reply_epoch", 0) or 0)

        if after_quote_epoch != before_reply_epoch:
            state["next_reply_lane_priority"] = "normal"
            save_state(state)
            log.info("Test-cycle quote-tweet lane posted; next reply-lane priority=normal")
        else:
            _normal_status, safety_stopped = run_test_reply_action(
                "normal",
                maybe_reply_to_mentions,
            )
            if safety_stopped:
                return 0
            if finish_if_ambiguity_blocked():
                return 0
            after_reply_epoch = int(state.get("last_reply_epoch", 0) or 0)
            if after_reply_epoch != after_quote_epoch:
                state["next_reply_lane_priority"] = "quote"
                save_state(state)
                log.info("Test-cycle normal/hot-post lane posted; next reply-lane priority=quote")
    else:
        _normal_status, safety_stopped = run_test_reply_action(
            "normal",
            maybe_reply_to_mentions,
        )
        if safety_stopped:
            return 0
        if finish_if_ambiguity_blocked():
            return 0
        after_reply_epoch = int(state.get("last_reply_epoch", 0) or 0)

        if after_reply_epoch != before_reply_epoch:
            state["next_reply_lane_priority"] = "quote"
            save_state(state)
            log.info("Test-cycle normal/hot-post lane posted; next reply-lane priority=quote")
        else:
            quote_status, safety_stopped = run_test_reply_action(
                "quote_tweet",
                maybe_reply_to_quote_tweets,
            )
            if safety_stopped:
                return 0
            if finish_if_ambiguity_blocked():
                return 0
            log.info("Test-cycle quote-tweet check status=%s", quote_status)
            log_event("quote_check_status", status=quote_status, priority="test_cycle")
            after_quote_epoch = int(state.get("last_reply_epoch", 0) or 0)
            if after_quote_epoch != after_reply_epoch:
                state["next_reply_lane_priority"] = "normal"
                save_state(state)
                log.info("Test-cycle quote-tweet lane posted; next reply-lane priority=normal")

    save_state(state)
    log.info("Test cycle finished")
    return 0


def run_test_main_tick(
    *,
    acquire_instance_lock: Any,
    ambiguous_remote_post_is_blocking: Any,
    block_if_ambiguous_remote_post: Any,
    load_runtime_state: Any,
    log: Any,
    now_epoch: Any,
    reconcile_runtime_historical_context_state: Any,
    report_bot_health_progress: Any,
    require_established_installation_after_ledger_recovery: Any,
    require_production_bootstrap: Any,
    require_test_mode: Any,
    run_reply_lane_checks_for_tick: Any,
    save_state: Any,
    scheduler_epoch_from_state: Any,
    wait_for_durable_barrier_before_one_shot_exit: Any,
) -> int:
    """Run the production reply-lane tick once for local integration tests."""
    if not require_test_mode("--test-main-tick"):
        return 2
    require_production_bootstrap()
    report_bot_health_progress("startup")

    acquire_instance_lock()
    report_bot_health_progress("recovery")
    require_established_installation_after_ledger_recovery()
    reconcile_runtime_historical_context_state()
    block_if_ambiguous_remote_post()

    log.info("Running one test production reply-lane tick")
    state = load_runtime_state()
    current = now_epoch()
    last_reply_check_epoch, reply_epoch_changed = scheduler_epoch_from_state(
        state,
        "last_reply_check_epoch",
        current=current,
    )
    last_quote_tweet_check_epoch, quote_epoch_changed = scheduler_epoch_from_state(
        state,
        "last_quote_tweet_check_epoch",
        current=current,
    )
    if reply_epoch_changed or quote_epoch_changed:
        save_state(state)

    report_bot_health_progress("main_loop", loop_started=True)
    report_bot_health_progress("reply_checks")
    run_reply_lane_checks_for_tick(
        state,
        current,
        last_reply_check_epoch=last_reply_check_epoch,
        last_quote_tweet_check_epoch=last_quote_tweet_check_epoch,
    )
    report_bot_health_progress("main_loop")
    if ambiguous_remote_post_is_blocking():
        wait_for_durable_barrier_before_one_shot_exit(
            lane="production_reply_tick",
        )
        return 0

    save_state(state)
    log.info("Test production reply-lane tick finished")
    report_bot_health_progress("shutdown", loop_completed=True)
    return 0


def require_test_mode(
    command_name: str,
    *,
    IMPORT_TIME_TEST_MODE: Any,
    log: Any,
) -> bool:
    """Require the immutable import-time test-mode safety configuration."""
    if not IMPORT_TIME_TEST_MODE:
        log.error("%s requires MRS_TEST_MODE=1 before bot import", command_name)
        return False
    return True


def wait_for_durable_barrier_before_one_shot_exit(
    *,
    lane: str,
    durable_remote_write_safety_barrier_exists: Any,
    log: Any,
    remote_write_safety_incident_is_latched: Any,
    sleep: Any,
) -> None:
    """Keep a one-shot posting process alive while its only barrier is memory."""
    if (
        not remote_write_safety_incident_is_latched()
        or durable_remote_write_safety_barrier_exists()
    ):
        return
    log.critical(
        "The one-shot %s command cannot exit because its only remote-write safety "
        "barrier is process-local. Create and verify a durable reconciliation "
        "marker before terminating this process.",
        lane,
    )
    while not durable_remote_write_safety_barrier_exists():
        sleep(60)
    log.critical(
        "A durable remote-write safety marker is now present for one-shot lane=%s; "
        "process exit is restart-safe",
        lane,
    )


def run_test_post_quote(
    *,
    AmbiguousRemotePostOutcome: Any,
    ApiError: Any,
    ConfirmedPostLocalPersistenceError: Any,
    LINES_FILE: Any,
    UnrecoverableConfirmedPostPersistenceError: Any,
    acquire_instance_lock: Any,
    block_if_ambiguous_remote_post: Any,
    current_image_paths: Any,
    lane_paused: Any,
    load_image_used_basenames: Any,
    load_quote_used_hashes: Any,
    load_runtime_state: Any,
    log: Any,
    post_random_quote: Any,
    prepare_test_main_post_state: Any,
    reconcile_runtime_historical_context_state: Any,
    record_api_error: Any,
    require_established_installation_after_ledger_recovery: Any,
    require_production_bootstrap: Any,
    require_test_mode: Any,
    save_state: Any,
    wait_for_durable_barrier_before_one_shot_exit: Any,
) -> int:
    """Run one quote/image post cycle for local integration tests."""
    if not require_test_mode("--test-post-quote"):
        return 2
    require_production_bootstrap()

    acquire_instance_lock()
    require_established_installation_after_ledger_recovery()
    reconcile_runtime_historical_context_state()
    block_if_ambiguous_remote_post(
        allow_confirmed_pending_schedule_reconciliation=True
    )

    log.info("Running one test quote/image post cycle")
    state = load_runtime_state()
    prepare_test_main_post_state(state)

    if lane_paused("disable_quote_posts"):
        log.warning("Skipping test quote/image post due to runtime control file")
        save_state(state)
        return 0

    with open(LINES_FILE, encoding="utf-8") as f:
        quote_lines_for_history = f.readlines()
    lines_used = load_quote_used_hashes(quote_lines_for_history)
    images_used = load_image_used_basenames(current_image_paths())

    try:
        post_random_quote(lines_used, images_used, state)
    except UnrecoverableConfirmedPostPersistenceError:
        log.critical(
            "REMOTE X POST WAS CONFIRMED WITHOUT A COMPLETE DURABLE LOCAL "
            "REPRESENTATION. The one-shot quote process must not exit while only "
            "its in-memory safety latch survives.",
            exc_info=True,
        )
        wait_for_durable_barrier_before_one_shot_exit(lane="quote_image")
        return 3
    except ConfirmedPostLocalPersistenceError:
        log.critical(
            "REMOTE X POST WAS CONFIRMED; DO NOT RETRY MANUALLY. "
            "Test quote/image local persistence/recovery needs attention.",
            exc_info=True,
        )
        save_state(state)
        return 3
    except AmbiguousRemotePostOutcome as exc:
        log.critical(
            "The one-shot quote remote outcome is ambiguous; refusing normal exit "
            "while only an in-memory safety latch survives.",
            exc_info=True,
        )
        wait_for_durable_barrier_before_one_shot_exit(lane="quote_image")
        record_api_error(state, exc, "x", scope="write")
        save_state(state)
        return 1
    except ApiError as exc:
        log.exception("Test quote/image post failed due to API error")
        record_api_error(state, exc, "x", scope="write")
        save_state(state)
        return 1
    except Exception:
        log.exception("Test quote/image post failed unexpectedly")
        save_state(state)
        return 1

    log.info("Test quote/image post cycle finished")
    return 0


def run_test_post_meme(
    *,
    AmbiguousRemotePostOutcome: Any,
    ApiError: Any,
    ConfirmedPostLocalPersistenceError: Any,
    UnrecoverableConfirmedPostPersistenceError: Any,
    acquire_instance_lock: Any,
    block_if_ambiguous_remote_post: Any,
    lane_paused: Any,
    load_runtime_state: Any,
    log: Any,
    post_next_meme: Any,
    prepare_test_main_post_state: Any,
    reconcile_runtime_historical_context_state: Any,
    record_api_error: Any,
    require_established_installation_after_ledger_recovery: Any,
    require_production_bootstrap: Any,
    require_test_mode: Any,
    save_state: Any,
    wait_for_durable_barrier_before_one_shot_exit: Any,
) -> int:
    """Run one daily meme post cycle for local integration tests."""
    if not require_test_mode("--test-post-meme"):
        return 2
    require_production_bootstrap()

    acquire_instance_lock()
    require_established_installation_after_ledger_recovery()
    reconcile_runtime_historical_context_state()
    block_if_ambiguous_remote_post()

    log.info("Running one test daily meme post cycle")
    state = load_runtime_state()
    prepare_test_main_post_state(state)

    if lane_paused("disable_meme_posts"):
        log.warning("Skipping test daily meme post due to runtime control file")
        save_state(state)
        return 0

    try:
        post_next_meme(state)
    except UnrecoverableConfirmedPostPersistenceError:
        log.critical(
            "REMOTE X POST WAS CONFIRMED WITHOUT A COMPLETE DURABLE LOCAL "
            "REPRESENTATION. The one-shot meme process must not exit while only "
            "its in-memory safety latch survives.",
            exc_info=True,
        )
        wait_for_durable_barrier_before_one_shot_exit(lane="daily_meme")
        return 3
    except ConfirmedPostLocalPersistenceError:
        log.critical(
            "REMOTE X POST WAS CONFIRMED; DO NOT RETRY MANUALLY. "
            "Test daily meme local persistence/recovery needs attention.",
            exc_info=True,
        )
        save_state(state)
        return 3
    except AmbiguousRemotePostOutcome as exc:
        log.critical(
            "The one-shot meme remote outcome is ambiguous; refusing normal exit "
            "while only an in-memory safety latch survives.",
            exc_info=True,
        )
        wait_for_durable_barrier_before_one_shot_exit(lane="daily_meme")
        record_api_error(state, exc, "x", scope="write")
        save_state(state)
        return 1
    except ApiError as exc:
        log.exception("Test daily meme post failed due to API error")
        record_api_error(state, exc, "x", scope="write")
        save_state(state)
        return 1
    except Exception:
        log.exception("Test daily meme post failed unexpectedly")
        save_state(state)
        return 1

    log.info("Test daily meme post cycle finished")
    return 0


def run_cli(
    argv: list[str] | tuple[str, ...] | None = None,
    *,
    CLI_USAGE: Any,
    CliUsageError: Any,
    IMPORT_TIME_CLI_ARGUMENTS: Any,
    IMPORT_TIME_TEST_MODE: Any,
    TEST_MODE_REQUIRED_CLI_FLAGS: Any,
    initialise_installation: Any,
    main: Any,
    parse_cli_mode: Any,
    production_bootstrap: Any,
    run_self_test: Any,
    run_test_cycle: Any,
    run_test_main_tick: Any,
    run_test_post_meme: Any,
    run_test_post_quote: Any,
    sys: Any,
) -> int | None:
    """Validate one complete command line, then bootstrap and dispatch it."""

    process_arguments = tuple(sys.argv[1:])
    arguments = process_arguments if argv is None else tuple(argv)
    try:
        if process_arguments != IMPORT_TIME_CLI_ARGUMENTS:
            raise CliUsageError("process argv changed after module import")
        if argv is not None and arguments != IMPORT_TIME_CLI_ARGUMENTS:
            raise CliUsageError(
                "explicit argv must exactly match the import-time command line"
            )
        mode = parse_cli_mode(IMPORT_TIME_CLI_ARGUMENTS)
        if mode in TEST_MODE_REQUIRED_CLI_FLAGS and not IMPORT_TIME_TEST_MODE:
            raise CliUsageError(
                f"{mode} requires MRS_TEST_MODE=1 before bot import"
            )
    except CliUsageError as exc:
        print(f"{CLI_USAGE}\nmrsMThatcher2.py: error: {exc}", file=sys.stderr)
        return 2

    # Argument validation is deliberately complete before this call.  No
    # invalid or ambiguous argv may reach configuration loading, the instance
    # lock, durable state, or any remote-operation boundary.
    production_bootstrap()
    if mode == "--initialise":
        return initialise_installation()
    if mode == "--self-test":
        return run_self_test()
    if mode == "--test-cycle":
        return run_test_cycle()
    if mode == "--test-main-tick":
        return run_test_main_tick()
    if mode == "--test-post-quote":
        return run_test_post_quote()
    if mode == "--test-post-meme":
        return run_test_post_meme()
    main()
    return None

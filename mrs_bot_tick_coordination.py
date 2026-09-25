"""Own one finite production iteration and its reply and main-post policy.

The root supplies process authorities and fresh application-assembly suppliers.
Construction performs no clock, storage, control or provider operation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger
from typing import TYPE_CHECKING, Any, Protocol

from mrs_bot_reply_cycle_interfaces import (
    NORMAL_CHECK_STATUS_POSTED,
    NORMAL_CHECK_STATUS_SKIPPED_SPACING,
    QUOTE_CHECK_STATUS_POSTED,
    QUOTE_CHECK_STATUS_SKIPPED_SPACING,
)

if TYPE_CHECKING:
    from mrs_bot_api_cooldowns import ApiCooldowns
    from mrs_bot_main_post_assembly import MainPostAssembly
    from mrs_bot_reply_assembly import ReplyAssembly
    from mrs_bot_runtime_control import RuntimeControls
    from mrs_bot_runtime_state_helpers import QuoteSchedule
    from mrs_bot_state_generation import StateCommitProof


class ResumeSourceRetirement(Protocol):
    """Resume local retirement under the already-read maintenance snapshot."""

    def __call__(self, *, maintenance_paused: bool) -> bool:
        """Return whether a retirement was resumed."""
        ...


class SchedulerEpoch(Protocol):
    """Read and repair one scheduling epoch without hiding its current clock."""

    def __call__(
        self, state: dict[str, Any], key: str, *, current: int | None = None,
    ) -> tuple[int, bool]:
        """Return the usable epoch and whether the state was changed."""
        ...


class ReportHealth(Protocol):
    """Emit observational stage health with the supported named flags."""

    def __call__(
        self, phase: str, *, paused: bool | None = None,
        remote_write_blocked: bool | None = None,
        loop_started: bool = False, loop_completed: bool = False,
    ) -> None:
        """Report stage progress."""
        ...


class ProcessHistoricalContext(Protocol):
    """Run one due context obligation with the current state reference."""

    def __call__(
        self, *, limit: int = 1, runtime_state: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return attempted context obligations."""
        ...


@dataclass(frozen=True)
class RuntimeSettings:
    """Scheduling values fixed by the bootstrap configuration."""

    enable_auto_replies: bool
    enable_quote_tweet_checks: bool
    enable_daily_meme_posts: bool
    minimum_reply_spacing: int
    reply_check_interval: int
    quote_check_interval: int
    quote_spacing_retry: int
    minimum_meme_after_quote: int


@dataclass(frozen=True)
class RuntimeErrors:
    """Current exception identities at the runtime boundary."""

    ambiguous_post: type[Exception]
    unrecoverable_reply: type[Exception]
    unrecoverable_main_post: type[Exception]
    confirmed_main_post: type[Exception]
    api_error: type[Exception]


def sanitize_next_reply_lane_priority(state: dict[str, Any], *, log: Logger) -> bool:
    """Sanitise next reply lane priority in the original state."""
    priority = str(state.get("next_reply_lane_priority", "normal") or "normal")
    if priority in {"normal", "quote"}:
        if state.get("next_reply_lane_priority") != priority:
            state["next_reply_lane_priority"] = priority
            return True
        return False

    log.warning("Invalid next_reply_lane_priority=%r; using normal", state.get("next_reply_lane_priority"))
    state["next_reply_lane_priority"] = "normal"
    return True


class RuntimeCoordinator:
    """Sequence one production iteration using shared state and fresh lane owners."""

    def __init__(
        self, *,
        settings: RuntimeSettings,
        errors: RuntimeErrors,
        controls: RuntimeControls,
        lane_controls: Callable[[], RuntimeControls],
        cooldowns: Callable[[], ApiCooldowns],
        quote_schedule: Callable[[], QuoteSchedule],
        reply_assembly: Callable[[], ReplyAssembly],
        main_post_assembly: Callable[[], MainPostAssembly],
        now_epoch: Callable[[], int],
        scheduler_epoch_from_state: SchedulerEpoch,
        save_state: Callable[[dict[str, Any]], StateCommitProof],
        log: Logger,
        log_event: Callable[..., None],
        report_health: ReportHealth,
        resume_media_retirement: Callable[[], bool],
        resume_source_retirement: ResumeSourceRetirement,
        reconcile_confirmed_transactions: Callable[[set[str], set[str], dict[str, Any]], dict[str, bool]],
        ambiguous_remote_post_is_blocking: Callable[[], bool],
        durable_remote_write_safety_barrier_exists: Callable[[], bool],
        remote_write_safety_protocol_is_active: Callable[[], bool],
        process_historical_context: ProcessHistoricalContext,
        maintenance_pause_logged: bool = False,
    ) -> None:
        """Bind boundaries without retaining any operation's assembly or runner."""
        self.settings = settings
        self.errors = errors
        self.controls = controls
        self.lane_controls = lane_controls
        self.cooldowns = cooldowns
        self.quote_schedule = quote_schedule
        self.reply_assembly = reply_assembly
        self.main_post_assembly = main_post_assembly
        self.now_epoch = now_epoch
        self.scheduler_epoch_from_state = scheduler_epoch_from_state
        self.save_state = save_state
        self.log = log
        self.log_event = log_event
        self.report_health = report_health
        self.resume_media_retirement = resume_media_retirement
        self.resume_source_retirement = resume_source_retirement
        self.reconcile_confirmed_transactions = reconcile_confirmed_transactions
        self.ambiguous_remote_post_is_blocking = ambiguous_remote_post_is_blocking
        self.durable_remote_write_safety_barrier_exists = durable_remote_write_safety_barrier_exists
        self.remote_write_safety_protocol_is_active = remote_write_safety_protocol_is_active
        self.process_historical_context = process_historical_context
        self.maintenance_pause_logged = maintenance_pause_logged
        self.ambiguity_pause_logged = False

    def run_continuously(
        self, lines_used: set[str], images_used: set[str], state: dict[str, Any],
        *, sleep: Callable[[int], None],
    ) -> None:
        """Drive the same finite operation and wait only when it requests a wait."""
        while True:
            wait_seconds = self.run_once(lines_used, images_used, state)
            if wait_seconds is not None:
                sleep(wait_seconds)

    def run_once(
        self, lines_used: set[str], images_used: set[str], state: dict[str, Any],
    ) -> int | None:
        """Run exactly one production iteration; return wait seconds or None to continue."""
        log = self.log
        health = self.report_health
        health("main_loop", loop_started=True)
        maintenance_paused = self.controls.global_paused()
        if not maintenance_paused:
            try:
                self.resume_media_retirement()
            except Exception:
                log.critical(
                    "Interrupted confirmed-media retirement could not be "
                    "resumed; every remote lane remains blocked",
                    exc_info=True,
                )
        try:
            self.resume_source_retirement(maintenance_paused=maintenance_paused)
        except Exception:
            log.critical(
                "Interrupted source-receipt retirement could not be resumed; "
                "all remote lanes remain blocked",
                exc_info=True,
            )
        if not maintenance_paused:
            try:
                reconciled = self.reconcile_confirmed_transactions(
                    lines_used, images_used, state,
                )
            except Exception:
                log.critical(
                    "A locally confirmed remote transaction could not be "
                    "reconciled before the global barrier; all remote lanes "
                    "remain blocked",
                    exc_info=True,
                )
            else:
                if any(reconciled.values()):
                    log.warning(
                        "Completed local confirmed-transaction recovery before "
                        "remote scheduling: %s",
                        {key: value for key, value in reconciled.items() if value},
                    )

        ambiguity_blocked = self.maintain_global_remote_write_barrier_tick()
        if maintenance_paused:
            if not self.maintenance_pause_logged:
                log.warning(
                    "Global runtime control pause is active; all remote-write lanes "
                    "remain idle"
                )
            self.maintenance_pause_logged = True
            health(
                "paused", paused=True, remote_write_blocked=ambiguity_blocked,
                loop_completed=True,
            )
            return 60
        if self.maintenance_pause_logged:
            log.info("Global runtime control pause cleared; resuming scheduled lanes")
        self.maintenance_pause_logged = False
        health("main_loop", paused=False)

        if ambiguity_blocked:
            health(
                "remote_write_blocked", remote_write_blocked=True,
                loop_completed=True,
            )
            return 60
        health("main_loop", remote_write_blocked=False)

        current = self.now_epoch()
        log.debug("Main loop tick. epoch=%s", current)
        health("historical_context")
        self.process_historical_context(limit=1, runtime_state=state)
        health("main_loop")
        if self._stage_blocked():
            return None

        health("reply_checks")
        self.run_reply_lane_checks_for_tick(state, current)
        health("main_loop")
        if self._stage_blocked():
            return None

        self.run_due_quote_post_for_tick(lines_used, images_used, state, current)
        if self._stage_blocked():
            return None

        self.run_due_meme_post_for_tick(state, current)
        log.debug("Sleeping for 60 seconds")
        health("sleep", loop_completed=True)
        return 60

    def _stage_blocked(self) -> bool:
        """Report a newly observed barrier before the next stage."""
        if not self.ambiguous_remote_post_is_blocking():
            return False
        self.report_health(
            "remote_write_blocked", remote_write_blocked=True,
            loop_completed=True,
        )
        return True

    def maintain_global_remote_write_barrier_tick(self) -> bool:
        """Recheck durability on every blocked iteration and log the barrier once."""
        if not self.ambiguous_remote_post_is_blocking():
            self.ambiguity_pause_logged = False
            return False
        protocol_active = self.remote_write_safety_protocol_is_active()
        try:
            # This may also deliver retained SIGINT after a durable barrier.
            durable_marker_confirmed = self.durable_remote_write_safety_barrier_exists()
        except Exception:
            durable_marker_confirmed = False
            self.log.critical(
                "The remote-write safety marker could not be inspected; the "
                "process will remain latched and must not be restarted",
                exc_info=True,
            )
        if self.ambiguity_pause_logged:
            return True
        if not protocol_active:
            self.log.critical(
                "All remote posting and reply lanes are paused because the "
                "restart-persistent remote-write protocol is not activated or its "
                "sentinel is invalid; stopped clean-state activation or repair is "
                "required"
            )
        elif durable_marker_confirmed:
            self.log.critical(
                "All remote posting and reply lanes are paused by the durable "
                "remote-write safety barrier; manual reconciliation is required "
                "before a controlled restart"
            )
        else:
            self.log.critical(
                "All remote posting and reply lanes are paused by an unresolved "
                "transaction receipt, marker, or process latch; do not restart "
                "before exact reconciliation"
            )
        self.ambiguity_pause_logged = True
        return True

    def run_reply_lane_checks_for_tick(
        self, state: dict[str, Any], current: int,
    ) -> tuple[int, int]:
        """Arbitrate reply lanes at the supplied tick epoch and repair scheduler state."""
        settings = self.settings
        AmbiguousRemotePostOutcome = self.errors.ambiguous_post
        UnrecoverableConfirmedReplyPersistenceError = self.errors.unrecoverable_reply
        ENABLE_AUTO_REPLIES = settings.enable_auto_replies
        ENABLE_QUOTE_TWEET_CHECKS = settings.enable_quote_tweet_checks
        MIN_SECONDS_BETWEEN_REPLIES = settings.minimum_reply_spacing
        QUOTE_CHECK_EVERY_SECONDS = settings.quote_check_interval
        QUOTE_CHECK_SPACING_RETRY_SECONDS = settings.quote_spacing_retry
        REPLY_CHECK_EVERY_SECONDS = settings.reply_check_interval
        scheduler_epoch_from_state = self.scheduler_epoch_from_state
        save_state = self.save_state
        log_event = self.log_event
        log = self.log
        ambiguity_blocked = False
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

        reply_lane_priority = str(state.get("next_reply_lane_priority", "normal") or "normal")

        seconds_since_last_reply = current - int(state.get("last_reply_epoch", 0) or 0)
        reply_spacing_open = seconds_since_last_reply >= MIN_SECONDS_BETWEEN_REPLIES
        mention_check_due = ENABLE_AUTO_REPLIES and current - last_reply_check_epoch >= REPLY_CHECK_EVERY_SECONDS
        quote_check_due = (
            ENABLE_QUOTE_TWEET_CHECKS
            and current - last_quote_tweet_check_epoch >= QUOTE_CHECK_EVERY_SECONDS
        )

        def run_normal_check(*, forced: bool = False) -> bool:
            nonlocal last_reply_check_epoch, ambiguity_blocked

            if forced:
                log.info(
                    "Quote-tweet check is due, but normal/hot-post reply lane has priority; "
                    "running normal reply check first"
                )
            else:
                log.info("Due to check mentions")

            try:
                normal_check_status = self.reply_assembly().run_normal(state)
            except (
                AmbiguousRemotePostOutcome,
                UnrecoverableConfirmedReplyPersistenceError,
            ):
                ambiguity_blocked = True
                log.critical(
                    "Normal reply lane stopped by the global remote-write safety barrier"
                )
                return False
            log.info("Normal/hot-post reply check status=%s", normal_check_status)
            if self.ambiguous_remote_post_is_blocking():
                ambiguity_blocked = True
                log.critical("Normal reply lane created an ambiguous-post barrier; skipping all later lanes")
                return False

            if normal_check_status != NORMAL_CHECK_STATUS_SKIPPED_SPACING:
                last_reply_check_epoch = current
                state["last_reply_check_epoch"] = current
                save_state(state)
            else:
                log.info(
                    "Normal/hot-post reply check skipped only because of reply spacing; "
                    "normal check interval not consumed"
                )

            if normal_check_status == NORMAL_CHECK_STATUS_POSTED:
                state["next_reply_lane_priority"] = "quote"
                save_state(state)
                log.info("Normal/hot-post reply lane posted; next reply-lane priority=quote")
                return True

            if forced:
                log.info("Normal/hot-post reply lane did not post; quote-tweet lane may use this slot")

            return False

        def run_quote_check() -> bool:
            nonlocal last_quote_tweet_check_epoch, ambiguity_blocked

            log.info("Due to check quote tweets")
            priority_at_check = str(state.get("next_reply_lane_priority", reply_lane_priority) or reply_lane_priority)
            try:
                quote_check_status = self.reply_assembly().run_quote(state)
            except (
                AmbiguousRemotePostOutcome,
                UnrecoverableConfirmedReplyPersistenceError,
            ):
                ambiguity_blocked = True
                log.critical(
                    "Quote-tweet lane stopped by the global remote-write safety barrier"
                )
                return False

            log.info("Quote-tweet check status=%s", quote_check_status)
            if self.ambiguous_remote_post_is_blocking():
                ambiguity_blocked = True
                log.critical("Quote-tweet lane created an ambiguous-post barrier; skipping all later lanes")
                return False
            log_event("quote_check_status", status=quote_check_status, priority=priority_at_check)

            if quote_check_status == QUOTE_CHECK_STATUS_POSTED:
                state["next_reply_lane_priority"] = "normal"
                save_state(state)
                log.info("Quote-tweet reply lane posted; next reply-lane priority=normal")

            if quote_check_status != QUOTE_CHECK_STATUS_SKIPPED_SPACING:
                last_quote_tweet_check_epoch = current
                state["last_quote_tweet_check_epoch"] = current
                save_state(state)
            else:
                retry_epoch = current - QUOTE_CHECK_EVERY_SECONDS + QUOTE_CHECK_SPACING_RETRY_SECONDS
                last_quote_tweet_check_epoch = retry_epoch
                state["last_quote_tweet_check_epoch"] = retry_epoch
                save_state(state)

                log.info(
                    "Quote-tweet check skipped only because of reply spacing; "
                    "will retry in about %d seconds",
                    QUOTE_CHECK_SPACING_RETRY_SECONDS,
                )

            return quote_check_status == QUOTE_CHECK_STATUS_POSTED

        if reply_spacing_open and quote_check_due and reply_lane_priority == "quote":
            quote_posted = run_quote_check()
            if not quote_posted and mention_check_due and not ambiguity_blocked:
                run_normal_check()
        elif mention_check_due:
            normal_posted = run_normal_check()
            if not normal_posted and quote_check_due and not ambiguity_blocked:
                run_quote_check()
        elif (
            reply_spacing_open
            and quote_check_due
            and reply_lane_priority == "normal"
            and ENABLE_AUTO_REPLIES
        ):
            normal_posted = run_normal_check(forced=True)
            if not normal_posted and not ambiguity_blocked:
                run_quote_check()
        elif quote_check_due:
            run_quote_check()
        else:
            log.debug(
                "Not due to check mentions. seconds_until_next=%s",
                max(0, REPLY_CHECK_EVERY_SECONDS - (current - last_reply_check_epoch)),
            )
            log.debug(
                "Not due to check quote tweets. seconds_until_next=%s",
                max(0, QUOTE_CHECK_EVERY_SECONDS - (current - last_quote_tweet_check_epoch)),
            )

        return last_reply_check_epoch, last_quote_tweet_check_epoch

    def run_due_quote_post_for_tick(
        self, lines_used: set[str], images_used: set[str],
        state: dict[str, Any], current: int,
    ) -> None:
        """Handle quote timing and retries after the main loop's safety gates."""
        cooldowns = self.cooldowns()
        controls = self.lane_controls()
        quote_schedule = self.quote_schedule()
        next_quote_epoch = int(state.get("next_quote_post_epoch", 0))
        if current >= next_quote_epoch:
            self.log.info("Due to post quote/image")

            if controls.lane_paused("disable_quote_posts"):
                self.log.warning("Skipping quote/image post due to runtime control file; retrying in 5 minutes")
                state["next_quote_post_epoch"] = current + 300
                self.save_state(state)
            elif cooldowns.active(state, scope="write"):
                self.log.warning("Skipping quote/image post due to X write API cooldown")
                quote_schedule.schedule(state, current)
            else:
                quote_posted = False
                self.report_health("quote_post")
                try:
                    self.main_post_assembly().quote_runner().post(lines_used, images_used, state)
                    quote_posted = True
                except self.errors.unrecoverable_main_post:
                    quote_posted = True
                    self.log.exception(
                        "Quote/image post was confirmed remotely but no complete durable "
                        "local representation survived; all remote writes are now blocked"
                    )
                except self.errors.confirmed_main_post:
                    quote_posted = True
                    self.log.exception("Quote/image post was confirmed remotely but local recovery/persistence failed; not scheduling an error retry")
                except self.errors.ambiguous_post:
                    quote_posted = True
                    self.log.exception(
                        "Quote/image remote outcome is ambiguous; the remote-write safety "
                        "barrier is active and no retry will be scheduled"
                    )
                except self.errors.api_error as e:
                    self.log.exception("Quote/image posting failed due to API error")
                    cooldowns.record_error(state, e, "x", scope="write")
                except Exception:
                    self.log.exception("Quote/image posting failed unexpectedly")
                self.report_health("main_loop")

                if not quote_posted:
                    quote_schedule.schedule(state, current)
        else:
            self.log.debug(
                "Not due to post quote/image. seconds_until_next=%s",
                max(0, next_quote_epoch - current),
            )


    def run_due_meme_post_for_tick(self, state: dict[str, Any], current: int) -> None:
        """Handle meme timing and retries after the main loop's safety gates."""
        cooldowns = self.cooldowns()
        controls = self.lane_controls()
        if self.settings.enable_daily_meme_posts:
            meme_schedule = self.main_post_assembly().meme_schedule()
            next_meme_epoch = int(state.get("next_meme_post_epoch", 0) or 0)

            if current >= next_meme_epoch:
                self.log.info("Due to post daily meme")

                seconds_since_quote = current - int(state.get("last_quote_post_epoch", 0) or 0)

                if controls.lane_paused("disable_meme_posts"):
                    self.log.warning("Skipping daily meme post due to runtime control file; retrying in 5 minutes")
                    meme_schedule.set_delay(state, epoch=current + 300, mode="delayed_runtime_control")
                elif seconds_since_quote < self.settings.minimum_meme_after_quote:
                    self.log.info(
                        "Meme post due, but delaying because last quote post was %d seconds ago",
                        seconds_since_quote,
                    )
                    meme_schedule.set_delay(state, epoch=current + 1800, mode="delayed_recent_quote")
                elif cooldowns.active(state, scope="write"):
                    self.log.warning("Skipping daily meme post due to X write API cooldown")
                    meme_schedule.set_delay(state, epoch=current + 3600, mode="delayed_write_api_cooldown")
                else:
                    self.report_health("meme_post")
                    try:
                        self.main_post_assembly().meme_runner().post(state)
                    except self.errors.unrecoverable_main_post:
                        self.log.exception(
                            "Daily meme post was confirmed remotely but no complete durable "
                            "local representation survived; all remote writes are now blocked"
                        )
                    except self.errors.confirmed_main_post:
                        self.log.exception("Daily meme post was confirmed remotely but local recovery/persistence failed; not scheduling an error retry")
                    except self.errors.ambiguous_post:
                        self.log.exception(
                            "Daily meme remote outcome is ambiguous; the remote-write safety "
                            "barrier is active and no retry will be scheduled"
                        )
                    except self.errors.api_error as e:
                        self.log.exception("Daily meme posting failed due to API error")
                        cooldowns.record_error(state, e, "x", scope="write")
                        meme_schedule.set_delay(state, epoch=current + 3600, mode="delayed_api_error")
                    except Exception:
                        self.log.exception("Daily meme posting failed unexpectedly")
                        meme_schedule.set_delay(state, epoch=current + 3600, mode="delayed_exception")
                    self.report_health("main_loop")
            else:
                self.log.debug(
                    "Not due to post daily meme. seconds_until_next=%s",
                    max(0, next_meme_epoch - current),
                )

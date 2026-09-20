"""Daily meme selection, calendar scheduling and transactional posting.

The coordinator supplies current owners, callbacks, configuration, logger and
exception authority on every call. MainPostPublication owns shared publication
and partial transaction progress with authorities bound at cycle entry. The
posting lane calls its MemeCatalog and MemeSchedule directly. Named stages,
preparation exception boundaries, schedule projections and meme-specific recovery
remain explicit here; metadata, receipt storage and persistence retain their
owners. Explicit runtime calls
may scan the supplied meme directory and mutate/save the caller's state or publish
through supplied callbacks. Imports perform no runtime work or configuration
access. MemeCatalog owns asset discovery, history-reset selection and summaries.
MemeSchedule binds current calendar/persistence policy for each root call and
invokes its owned operations directly without retaining caller state.
Standard-library regex, calendar and random
imports preserve the existing behavior and shared random stream.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from logging import Logger
from pathlib import Path
from typing import TYPE_CHECKING

from mrs_bot_runtime_state_helpers import apply_state_fields


if TYPE_CHECKING:
    from mrs_bot_main_post_publication import MainPostPublication


# A callback may return None before a later step fails. Keep that distinct from
# a recovery value whose producing step never completed.
_RECOVERY_VALUE_UNAVAILABLE = object()


def _confirmed_meme_schedule_fields(receipt: dict) -> dict:
    """Project a confirmed meme's required schedule fields in their read order."""
    return {
        "next_meme_post_epoch": int(receipt["next_meme_post_epoch"]),
        "meme_schedule_version": int(receipt["meme_schedule_version"]),
        "next_meme_schedule_mode": str(receipt["next_meme_schedule_mode"]),
        "next_meme_schedule_date": str(receipt["next_meme_schedule_date"]),
        "meme_anchor_quote_post_epoch": 0,
    }


def original_meme_filename(shortlist_path: Path) -> str:
    """Return the original meme filename."""
    name = shortlist_path.name

    prefix_patterns = [
        r"^\d{3}_impact\d+_share\d+_grade[A-D]_post_as_is_(.+)$",
        r"^\d{3}_score\d+_(?:high|medium|low)_(.+)$",
    ]

    for pattern in prefix_patterns:
        match = re.match(pattern, name)
        if match:
            return match.group(1)

    return name


@dataclass(frozen=True)
class MemeCatalog:
    """Own meme asset discovery, cycle selection and metadata summaries."""

    log: Logger
    directory: Path
    reset_when_exhausted: bool
    save_state: Callable

    def summary(
        self,
        shortlist_path: Path,
        analysis_index: dict[str, dict],
    ) -> str:
        """Build meme cache summary."""
        original_name = original_meme_filename(shortlist_path)
        item = analysis_index.get(original_name)

        if not item:
            self.log.warning(
                "No meme analysis found for shortlist file=%s original_name=%s",
                shortlist_path.name,
                original_name,
            )
            return f"Anti-socialist meme image. Original filename: {original_name}."

        description = str(item.get("grok_description", "")).strip()
        message = str(item.get("anti_socialist_message", "")).strip()
        ranking = item.get("ranking")
        shareability = str(item.get("shareability", "")).strip()

        parts: list[str] = []

        if description:
            parts.append(description)

        if message:
            parts.append(f"Anti-socialist message: {message}")

        metadata_parts: list[str] = []

        if ranking is not None:
            metadata_parts.append(f"ranking {ranking}")

        if shareability:
            metadata_parts.append(f"shareability {shareability}")

        if metadata_parts:
            parts.append("Analysis metadata: " + ", ".join(metadata_parts) + ".")

        return " ".join(parts).strip()

    def candidates(
        self,
    ) -> list[Path]:
        """List meme candidates."""
        if not self.directory.exists():
            self.log.warning("Meme directory does not exist: %s", self.directory)
            return []

        files = [
            p for p in self.directory.iterdir()
            if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        ]

        files.sort(key=lambda p: p.name)
        self.log.info("Found %d meme candidates in %s", len(files), self.directory)
        return files

    def choose(
        self,
        state: dict,
    ) -> Path | None:
        """Select next meme."""
        candidates = self.candidates()

        if not candidates:
            return None

        posted = set(str(x) for x in state.get("posted_meme_filenames", []))
        available = [p for p in candidates if p.name not in posted]

        if not available:
            self.log.info("All meme candidates have already been posted")

            if self.reset_when_exhausted:
                self.log.info("RESET_MEME_CYCLE_WHEN_ALL_POSTED=True, clearing meme history")
                state["posted_meme_filenames"] = []
                self.save_state(state)
                return candidates[0]

            return None

        return available[0]


@dataclass(frozen=True)
class MemeSchedule:
    """Own the current meme calendar, fallback and quote-anchor scheduling rules."""

    bound_datetime: Callable
    timezone: str
    now_epoch: Callable
    fallback_hour: int
    fallback_minute: int
    version: int
    modes: set[str]
    enabled: bool
    trigger_hour: int
    minimum_delay: int
    maximum_delay: int
    save_state: Callable
    log: Logger

    def datetime(self, epoch: int) -> datetime:
        """Interpret a meme schedule epoch in the production calendar zone."""

        return self.bound_datetime(int(epoch), self.timezone)

    def date_str(self, epoch: int | None = None) -> str:
        """Return a meme schedule date independent of the process's ambient TZ."""

        if epoch is None:
            epoch = self.now_epoch()
        return self.datetime(int(epoch)).strftime("%Y-%m-%d")

    def posted_on_date(
        self,
        state: dict,
        date_text: str,
    ) -> bool:
        """Return the meme posted on date."""
        last_epoch = int(state.get("last_meme_post_epoch", 0) or 0)
        if not last_epoch:
            return False
        return self.date_str(last_epoch) == date_text

    def next_fallback_epoch(
        self,
        state: dict,
        from_epoch: int | None = None,
    ) -> int:
        """Return the next meme fallback epoch."""
        if from_epoch is None:
            from_epoch = self.now_epoch()

        now_dt = self.datetime(int(from_epoch))
        target = now_dt.replace(
            hour=self.fallback_hour,
            minute=self.fallback_minute,
            second=0,
            microsecond=0,
        )

        target_date = target.strftime("%Y-%m-%d")

        if int(target.timestamp()) <= from_epoch or self.posted_on_date(state, target_date):
            target = target + timedelta(days=1)

        return int(target.timestamp())

    def next_fields(
        self,
        state: dict,
        from_epoch: int | None = None,
        mode: str = 'fallback',
    ) -> dict:
        """Return the next meme schedule fields."""
        next_epoch = self.next_fallback_epoch(state, from_epoch)
        return {
            "next_meme_post_epoch": next_epoch,
            "meme_schedule_version": self.version,
            "next_meme_schedule_mode": mode,
            "next_meme_schedule_date": self.date_str(next_epoch),
            "meme_anchor_quote_post_epoch": 0,
        }

    def delay_fields(
        self,
        epoch: int,
        mode: str,
    ) -> dict:
        """Return the meme delay schedule fields."""
        if mode not in self.modes or mode in {"", "after_first_quote_after_midday"}:
            raise ValueError(f"Unsupported non-quote meme delay schedule mode: {mode}")
        return {
            "next_meme_post_epoch": int(epoch),
            "meme_schedule_version": self.version,
            "next_meme_schedule_mode": mode,
            "next_meme_schedule_date": self.date_str(int(epoch)),
            "meme_anchor_quote_post_epoch": 0,
        }

    def set_delay(
        self,
        state: dict,
        *,
        epoch: int,
        mode: str,
        save: bool = True,
    ) -> None:
        """Set meme delay schedule."""
        apply_state_fields(state, self.delay_fields(epoch, mode))
        if save:
            self.save_state(state)

    def schedule_next(
        self,
        state: dict,
        from_epoch: int | None = None,
        mode: str = 'fallback',
        *,
        save: bool = True,
    ) -> None:
        """
        Schedule the fallback daily meme time. This is deliberately later than the
        preferred organic timing. If a quote/image post happens after midday first,
        self.maybe_after_quote() will replace this fallback with a
        random 35-75 minute delay after that post.
        """
        fields = self.next_fields(state, from_epoch, mode)
        apply_state_fields(state, fields)
        next_epoch = int(fields["next_meme_post_epoch"])
        if save:
            self.save_state(state)

        self.log.info(
            "Next meme fallback scheduled at %s mode=%s",
            datetime.fromtimestamp(next_epoch).strftime("%Y-%m-%d %H:%M:%S"),
            mode,
        )

    def ensure_initialized(self, state: dict) -> None:
        """Ensure meme schedule initialized."""
        if not self.enabled:
            return

        next_epoch = int(state.get("next_meme_post_epoch", 0) or 0)
        schedule_version = int(state.get("meme_schedule_version", 0) or 0)

        if schedule_version != self.version:
            self.log.info(
                "Migrating meme schedule state to version %s: after first quote/image post after %02d:00, fallback %02d:%02d",
                self.version,
                self.trigger_hour,
                self.fallback_hour,
                self.fallback_minute,
            )
            self.schedule_next(state, self.now_epoch(), mode="fallback_migrated")
            return

        if not next_epoch:
            self.log.info("No next_meme_post_epoch found; scheduling meme fallback")
            self.schedule_next(state, self.now_epoch(), mode="fallback_startup")
            return

        self.log.info(
            "Existing next_meme_post_epoch=%s, human=%s, mode=%s, schedule_date=%s",
            next_epoch,
            datetime.fromtimestamp(next_epoch).strftime("%Y-%m-%d %H:%M:%S"),
            state.get("next_meme_schedule_mode"),
            state.get("next_meme_schedule_date"),
        )

    def fields_after_quote(
        self,
        state: dict,
        quote_post_epoch: int | None = None,
        *,
        delay: int | None = None,
    ) -> dict:
        """Return the meme schedule fields after quote post."""
        if not self.enabled:
            return {}

        if quote_post_epoch is None:
            quote_post_epoch = self.now_epoch()

        quote_dt = self.datetime(int(quote_post_epoch))
        quote_date = quote_dt.strftime("%Y-%m-%d")

        if quote_dt.hour < self.trigger_hour:
            self.log.info(
                "Quote/image post was before meme trigger hour %02d:00; not scheduling daily meme from it",
                self.trigger_hour,
            )
            return {}

        if self.posted_on_date(state, quote_date):
            self.log.info("Daily meme already posted on %s; not scheduling another", quote_date)
            return {}

        next_epoch = int(state.get("next_meme_post_epoch", 0) or 0)
        next_mode = str(state.get("next_meme_schedule_mode", "") or "")

        if next_epoch:
            next_schedule_date = str(state.get("next_meme_schedule_date", "") or "")
            if next_schedule_date == quote_date and next_mode == "after_first_quote_after_midday":
                self.log.info(
                    "Daily meme already scheduled from first post after midday at %s; not rescheduling",
                    datetime.fromtimestamp(next_epoch).strftime("%Y-%m-%d %H:%M:%S"),
                )
                return {}

        if delay is None:
            delay = random.randint(self.minimum_delay, self.maximum_delay)
        scheduled_epoch = int(quote_post_epoch) + delay
        return {
            "next_meme_post_epoch": scheduled_epoch,
            "meme_schedule_version": self.version,
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "next_meme_schedule_date": quote_date,
            "meme_anchor_quote_post_epoch": int(quote_post_epoch),
        }

    def maybe_after_quote(
        self,
        state: dict,
        quote_post_epoch: int | None = None,
        *,
        save: bool = True,
    ) -> None:
        """Attempt to schedule meme after quote post."""
        fields = self.fields_after_quote(state, quote_post_epoch)
        if not fields:
            return
        apply_state_fields(state, fields)
        if save:
            self.save_state(state)

        if quote_post_epoch is None:
            quote_post_epoch = self.now_epoch()
        scheduled_epoch = int(fields["next_meme_post_epoch"])
        delay = scheduled_epoch - int(quote_post_epoch)

        self.log.info(
            "Daily meme scheduled for %s: %d seconds after first quote/image post after %02d:00",
            datetime.fromtimestamp(scheduled_epoch).strftime("%Y-%m-%d %H:%M:%S"),
            delay,
            self.trigger_hour,
        )


def run_daily_meme_stage(
    stage: str,
    operation,
    *,
    log: Logger,
    log_event: Callable,
):
    """Run one meme stage while preserving the original exception type."""
    try:
        return operation()
    except Exception as exc:
        log.error(
            "Daily meme stage failed. stage=%s error_type=%s error=%s",
            stage,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        log_event(
            "daily_meme_failure",
            status="failed",
            stage=stage,
            error_type=type(exc).__name__,
            reason=str(exc)[:500],
        )
        raise


def require_valid_meme_post_id(
    posted_id: object,
    *,
    valid_post_id: Callable,
) -> None:
    """Reject a meme response that lacks a confirmed numeric post identity."""
    if not valid_post_id(posted_id):
        raise RuntimeError(
            "Daily meme post did not return a valid post id; meme state unchanged"
        )


def post_next_meme(
    state: dict,
    *,
    publication: MainPostPublication,
    catalog: MemeCatalog,
    schedule: MemeSchedule,
    log: Logger,
    run_daily_meme_stage: Callable,
    block_if_ambiguous_remote_post: Callable,
    both_main_post_receipts_exist: Callable,
    REGULAR_POST_RECEIPT_FILE: Path,
    MEME_POST_RECEIPT_FILE: Path,
    InvalidMemePostReceipt: type[Exception],
    reconcile_meme_post_receipt: Callable,
    now_epoch: Callable,
    block_if_unresolved_regular_post_receipt: Callable,
    load_meme_analysis_index: Callable,
    upload_media: Callable,
    build_main_post_attempt: Callable,
    MEME_POST_TEXT: str,
    MEME_SCHEDULE_VERSION: int,
    MEME_FALLBACK_HOUR: int,
    MEME_FALLBACK_MINUTE: int,
    MAIN_POST_SCHEDULE_TIMEZONE: str,
    remove_main_post_attempt: Callable,
    durable_remote_write_safety_barrier_exists: Callable,
    log_event: Callable,
    ConfirmedPendingScheduleDurabilityUncertain: type[Exception],
    ConfirmedPostLocalPersistenceError: type[Exception],
    materialize_bound_meme_schedule_receipt: Callable,
    apply_state_fields: Callable,
    cache_tweet: Callable,
    MY_USER_ID: str,
    record_recent_own_post: Callable,
    save_state: Callable,
    confirmed_meme_emergency_representation_is_complete: Callable,
    StateBackupWriteError: type[Exception],
    json_file_matches: Callable,
    STATE_FILE: Path,
    latch_confirmed_post_persistence_failure: Callable,
    UnrecoverableConfirmedPostPersistenceError: type[Exception],
    load_meme_post_receipt: Callable,
    retire_lane_transport_journal_if_present: Callable,
    remove_meme_post_receipt: Callable,
    emit_account_root_posted: Callable,
) -> None:
    """Select and post the next daily meme transactionally."""
    log.info("Starting daily meme post cycle")
    run_daily_meme_stage(
        "remote_write_barrier",
        lambda: block_if_ambiguous_remote_post(
            allow_confirmed_pending_schedule_reconciliation=True
        ),
    )

    def validate_receipt_barriers() -> None:
        if both_main_post_receipts_exist():
            log.critical(
                "Both regular and meme confirmed-post receipts exist; refusing meme posting until manually inspected: %s %s",
                REGULAR_POST_RECEIPT_FILE,
                MEME_POST_RECEIPT_FILE,
            )
            raise InvalidMemePostReceipt("Both main-post receipts exist; manual recovery required")

    run_daily_meme_stage("receipt_barrier", validate_receipt_barriers)
    if run_daily_meme_stage(
        "meme_receipt_reconciliation",
        lambda: reconcile_meme_post_receipt(state),
    ):
        log.warning("Reconciled meme post receipt; not creating a second meme post in the same call")
        return
    current_meme_epoch = now_epoch()
    if schedule.posted_on_date(state, schedule.date_str(current_meme_epoch)):
        log.warning(
            "Daily meme already confirmed on the current local date; "
            "scheduling the next fallback without another X request"
        )
        run_daily_meme_stage(
            "same_day_duplicate_barrier",
            lambda: schedule.schedule_next(
                state,
                current_meme_epoch,
                mode="fallback",
            ),
        )
        return
    run_daily_meme_stage(
        "main_receipt_barrier",
        block_if_unresolved_regular_post_receipt,
    )

    meme_path = run_daily_meme_stage(
        "meme_eligibility_and_asset_selection",
        lambda: catalog.choose(state),
    )

    if not meme_path:
        log.info("No meme available to post")
        run_daily_meme_stage(
            "schedule_update",
            lambda: schedule.schedule_next(state),
        )
        return

    analysis_index = run_daily_meme_stage(
        "x_request_preparation",
        load_meme_analysis_index,
    )
    image_summary = run_daily_meme_stage(
        "x_request_preparation",
        lambda: catalog.summary(meme_path, analysis_index),
    )

    log.info("Posting meme image: %s", meme_path)
    log.debug("Meme image summary for cache: %r", image_summary)

    media_id = run_daily_meme_stage(
        "media_upload",
        lambda: upload_media(str(meme_path), lane="daily_meme"),
    )

    main_post_attempt = build_main_post_attempt(
        lane="daily_meme",
        text=MEME_POST_TEXT,
        media_ids=[media_id],
        made_with_ai=False,
        selected_identity={"meme_basename": meme_path.name},
        recovery_plan={
            "next_schedule_mode": "fallback",
            "meme_schedule_version": MEME_SCHEDULE_VERSION,
            "fallback_hour": MEME_FALLBACK_HOUR,
            "fallback_minute": MEME_FALLBACK_MINUTE,
            "image_summary": image_summary,
            "schedule_timezone": MAIN_POST_SCHEDULE_TIMEZONE,
        },
        attempt_epoch=current_meme_epoch,
    )
    publication.prepare(main_post_attempt)
    publication.begin_guard()
    try:
        posted_id = publication.send(
            text=MEME_POST_TEXT, media_id=media_id, made_with_ai=False,
        )
    except BaseException as remote_exc:
        publication.handle_remote_failure(remote_exc)
        raise

    meme_post_epoch = _RECOVERY_VALUE_UNAVAILABLE
    meme_schedule_fields = _RECOVERY_VALUE_UNAVAILABLE
    try:
        meme_post_epoch = publication.read_confirmation_epoch(posted_id)
        receipt = publication.confirm_pending_schedule(
            posted_id, meme_post_epoch, image_summary=image_summary,
        )
        meme_schedule_fields = _confirmed_meme_schedule_fields(receipt)
    except BaseException as receipt_exc:
        log.critical(
            "Confirmed meme post_id=%s but stage=meme_receipt_creation failed; attempting direct durable state save",
            posted_id,
            exc_info=True,
        )
        log_event(
            "daily_meme_failure",
            status="failed",
            stage="meme_receipt_creation",
            post_id=str(posted_id),
            error_type=type(receipt_exc).__name__,
            reason=str(receipt_exc)[:500],
        )
        if isinstance(
            receipt_exc,
            ConfirmedPendingScheduleDurabilityUncertain,
        ):
            if receipt_exc.durable_barrier:
                publication.release_guard()
            else:
                publication.retain_guard()
            raise
        if publication.pending_promoted:
            # Confirmation is already durable.  Leave the pending receipt for
            # local-only reconciliation; no scheduler path may create another
            # meme while it remains.
            publication.release_guard()
            if not isinstance(receipt_exc, Exception):
                raise
            raise ConfirmedPostLocalPersistenceError(
                f"Confirmed meme post {posted_id}; its durable pending-schedule "
                "receipt remains for local-only reconciliation"
            ) from receipt_exc
        emergency_state_write_succeeded = False
        emergency_state_complete = False
        try:
            if publication.pending_available:
                fallback_receipt = materialize_bound_meme_schedule_receipt(
                    publication.pending_receipt
                )
                meme_schedule_fields = _confirmed_meme_schedule_fields(fallback_receipt)
            state["last_main_post_id"] = str(posted_id)
            if meme_post_epoch is not _RECOVERY_VALUE_UNAVAILABLE:
                state["last_meme_post_epoch"] = meme_post_epoch
            posted = set(str(x) for x in state.get("posted_meme_filenames", []))
            posted.add(meme_path.name)
            state["posted_meme_filenames"] = sorted(posted)
            if meme_schedule_fields is not _RECOVERY_VALUE_UNAVAILABLE:
                apply_state_fields(state, meme_schedule_fields)
            try:
                cache_tweet(
                    state,
                    tweet_id=str(posted_id),
                    text=MEME_POST_TEXT,
                    author_id=str(MY_USER_ID),
                    conversation_id=str(posted_id),
                    referenced_tweets=[],
                    image_summary=image_summary,
                    post_type="daily_meme",
                )
                record_recent_own_post(state, str(posted_id))
            except Exception:
                log.critical("Emergency in-memory cache/recent update failed after confirmed meme post", exc_info=True)
            from mrs_bot_state_generation import record_receipt_commit
            record_receipt_commit(state, publication.attempt)
            commit_proof = save_state(state, durable=True)
            emergency_state_write_succeeded = True
            emergency_state_complete = confirmed_meme_emergency_representation_is_complete(
                post_id=str(posted_id),
                post_epoch=meme_post_epoch if meme_post_epoch is not _RECOVERY_VALUE_UNAVAILABLE else None,
                meme_basename=meme_path.name,
                state=state,
                main_post_attempt=publication.attempt,
            )
        except Exception as emergency_exc:
            if isinstance(emergency_exc, StateBackupWriteError) and json_file_matches(STATE_FILE, state, commit_proof=getattr(emergency_exc, "commit_proof", None)):
                commit_proof = emergency_exc.commit_proof
                emergency_state_write_succeeded = True
                emergency_state_complete = confirmed_meme_emergency_representation_is_complete(
                    post_id=str(posted_id),
                    post_epoch=meme_post_epoch if meme_post_epoch is not _RECOVERY_VALUE_UNAVAILABLE else None,
                    meme_basename=meme_path.name,
                    state=state,
                    main_post_attempt=publication.attempt,
                )
                log.warning(
                    "Emergency canonical state was committed after confirmed meme post, "
                    "but a later backup/finalisation step failed; using the canonical "
                    "durable state as the recovery representation",
                    exc_info=True,
                )
            else:
                log.critical("Emergency state persistence failed after confirmed meme post", exc_info=True)
        if not emergency_state_complete:
            incomplete_component = (
                "incomplete_meme_post_state"
                if emergency_state_write_succeeded
                else "state"
            )
            durable_barrier = latch_confirmed_post_persistence_failure(
                lane="daily_meme",
                post_id=str(posted_id),
                failure_components=["meme_post_receipt", incomplete_component],
            )
            if durable_barrier or durable_remote_write_safety_barrier_exists():
                publication.release_guard()
            else:
                publication.retain_guard()
            raise UnrecoverableConfirmedPostPersistenceError(
                f"Confirmed meme post {posted_id} has no complete durable recovery representation"
            ) from receipt_exc
        status_after_fallback, _current_after_fallback = load_meme_post_receipt()
        if status_after_fallback == "sending":
            retire_lane_transport_journal_if_present(
                commit_proof=commit_proof,
                receipt_path=MEME_POST_RECEIPT_FILE,
                receipt=publication.attempt,
                lane="daily_meme",
                post_id=str(posted_id),
            )
            remove_main_post_attempt(
                publication.attempt,
                sending_disposition="confirmed_state_fallback",
                commit_proof=commit_proof,
            )
        elif status_after_fallback != "valid":
            raise UnrecoverableConfirmedPostPersistenceError(
                f"Confirmed meme post {posted_id} has no stable receipt state "
                "after fallback persistence"
            ) from receipt_exc
        publication.release_guard()
        if not isinstance(receipt_exc, Exception):
            raise
        raise ConfirmedPostLocalPersistenceError(
            f"Confirmed meme post {posted_id} but failed writing recovery receipt"
        ) from receipt_exc

    publication.release_guard()

    try:
        state["last_main_post_id"] = str(posted_id)
        state["last_meme_post_epoch"] = meme_post_epoch
        posted = set(str(x) for x in state.get("posted_meme_filenames", []))
        posted.add(meme_path.name)
        state["posted_meme_filenames"] = sorted(posted)
        apply_state_fields(state, meme_schedule_fields)
        cache_tweet(
            state,
            tweet_id=str(posted_id),
            text=MEME_POST_TEXT,
            author_id=str(MY_USER_ID),
            conversation_id=str(posted_id),
            referenced_tweets=[],
            image_summary=image_summary,
            post_type="daily_meme",
        )
        record_recent_own_post(state, str(posted_id))
        from mrs_bot_state_generation import record_receipt_commit
        record_receipt_commit(state, receipt)
        commit_proof = save_state(state, durable=True)
    except Exception as exc:
        log.critical(
            "Confirmed meme post_id=%s but stage=durable_state_and_schedule_update failed; receipt remains for reconciliation",
            posted_id,
            exc_info=True,
        )
        log_event(
            "daily_meme_failure",
            status="failed",
            stage="durable_state_and_schedule_update",
            post_id=str(posted_id),
            error_type=type(exc).__name__,
            reason=str(exc)[:500],
        )
        raise ConfirmedPostLocalPersistenceError(f"Confirmed meme post {posted_id} but durable state save failed")
    try:
        retire_lane_transport_journal_if_present(
            commit_proof=commit_proof,
            receipt_path=MEME_POST_RECEIPT_FILE,
            receipt=receipt,
            lane="daily_meme",
            post_id=str(posted_id),
        )
        remove_meme_post_receipt(receipt, commit_proof=commit_proof)
    except Exception as exc:
        log.critical(
            "Confirmed meme post_id=%s but stage=meme_receipt_confirmation failed after durable state save",
            posted_id,
            exc_info=True,
        )
        log_event(
            "daily_meme_failure",
            status="failed",
            stage="meme_receipt_confirmation",
            post_id=str(posted_id),
            error_type=type(exc).__name__,
            reason=str(exc)[:500],
        )
        raise ConfirmedPostLocalPersistenceError(f"Confirmed meme post {posted_id} but receipt removal failed") from exc

    log_event("main_post_posted", lane="daily_meme", post_id=posted_id, filename=meme_path.name)
    emit_account_root_posted(
        lane="daily_meme",
        post_id=posted_id,
        public_text=MEME_POST_TEXT,
        image_summary=image_summary,
    )
    log.info("Daily meme posted successfully. posted_id=%s file=%s", posted_id, meme_path.name)

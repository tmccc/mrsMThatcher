#!/usr/bin/env python3
"""Exercise remote-write restart barriers in a literal Python interpreter.

The parent pytest process supplies an isolated state directory and the exact
candidate source root.  This helper never permits a network request: remote
transport functions are replaced with local sentinels before they are
exercised.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Callable


OUTPUT_PREFIX = "REMOTE_WRITE_SECOND_RESTART_JSON="
RESTART_BARRIER_BASENAME = "ambiguous_post_outcome.restart_barrier.json"
NEW_MARKER_VALUE = {
    "made_with_ai": False,
    "media_ids": [],
    "outcome": "ambiguous_remote_post",
    "recorded_at_epoch": 1_800_000_000,
    "reply_to_id": "",
    "schema_version": 1,
    "text_sha256": hashlib.sha256(
        b"Offline successor-first ambiguous post"
    ).hexdigest(),
}


class LocalTransportBoundary(BaseException):
    """Signal that a locally replaced transport boundary was reached."""


class SchedulerTicksComplete(BaseException):
    """Stop the synthetic daemon after the requested number of loop sleeps."""


def emit(value: dict[str, object]) -> None:
    """Write one canonical result record without relying on buffered I/O."""

    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    os.write(1, OUTPUT_PREFIX.encode("ascii") + payload + b"\n")


def import_candidate(root: Path, state_directory: Path):
    """Import the exact candidate module after validating the supplied roots."""

    root = root.resolve(strict=True)
    state_directory = state_directory.resolve(strict=True)
    sys.path.insert(0, str(root))

    import mrsMThatcher2 as bot

    source_path = Path(bot.__file__).resolve(strict=True)
    expected_source = root / "mrsMThatcher2.py"
    if source_path != expected_source:
        raise RuntimeError(
            "literal-process helper imported an unexpected mrsMThatcher2.py"
        )
    if Path(bot.BASE_DIR).resolve(strict=True) != state_directory:
        raise RuntimeError("literal-process helper did not use the isolated state root")
    return bot, source_path


def source_sha256(source_path: Path) -> str:
    """Return the imported runtime source hash."""

    return hashlib.sha256(source_path.read_bytes()).hexdigest()


def receipt_presence(bot) -> dict[str, bool]:
    """Report every receipt which could independently block remote writes."""

    return {
        "confirmed_reply": Path(bot.CONFIRMED_REPLY_RECEIPT_FILE).exists(),
        "historical_context": Path(
            bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
        ).exists(),
        "meme": Path(bot.MEME_POST_RECEIPT_FILE).exists(),
        "regular": Path(bot.REGULAR_POST_RECEIPT_FILE).exists(),
    }


def fault_and_hard_exit(bot, source_path: Path, state_directory: Path) -> int:
    """Remove the legacy marker during real directory fsync, then hard-exit."""

    marker = Path(bot.AMBIGUOUS_POST_OUTCOME_FILE)
    successor = state_directory / RESTART_BARRIER_BASENAME
    initial_seen = bool(bot._AMBIGUOUS_REMOTE_POST_SEEN)
    initial_uncertain = bool(bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN)
    real_fsync_parent_dir = bot.fsync_parent_dir
    injected = False

    bot.require_remote_write_marker_removal_protocol = lambda: None

    def remove_fsync_and_exit(path: Path, *, strict: bool = False) -> None:
        nonlocal injected
        resolved = Path(path)
        if resolved != marker or injected:
            real_fsync_parent_dir(path, strict=strict)
            return
        injected = True
        marker_before = os.lstat(marker)
        successor_before = None
        try:
            successor_before = os.lstat(successor)
        except FileNotFoundError:
            pass
        same_inode_before = bool(
            successor_before is not None
            and marker_before.st_dev == successor_before.st_dev
            and marker_before.st_ino == successor_before.st_ino
        )
        marker.unlink()
        real_fsync_parent_dir(path, strict=strict)
        successor_after = None
        try:
            successor_after = os.lstat(successor)
        except FileNotFoundError:
            pass
        emit(
            {
                "initial_durability_uncertain": initial_uncertain,
                "initial_remote_seen": initial_seen,
                "marker_observed_before_fault": bool(
                    bot._AMBIGUOUS_REMOTE_POST_SEEN
                ),
                "marker_removed": not os.path.lexists(marker),
                "phase": "fault_and_hard_exit",
                "real_parent_fsync_completed": True,
                "receipts_present": receipt_presence(bot),
                "source_sha256": source_sha256(source_path),
                "successor_nlink_after": (
                    int(successor_after.st_nlink)
                    if successor_after is not None
                    else None
                ),
                "successor_nlink_before": (
                    int(successor_before.st_nlink)
                    if successor_before is not None
                    else None
                ),
                "successor_ordinary_after": bool(
                    successor_after is not None
                    and stat.S_ISREG(successor_after.st_mode)
                ),
                "successor_present_after": successor_after is not None,
                "successor_present_before": successor_before is not None,
                "successor_same_inode_before": same_inode_before,
            }
        )
        os._exit(73)

    bot.fsync_parent_dir = remove_fsync_and_exit
    bot.durable_remote_write_safety_marker_exists()
    emit(
        {
            "error": "marker acknowledgement returned without fault injection",
            "phase": "fault_and_hard_exit",
            "source_sha256": source_sha256(source_path),
        }
    )
    return 74


def new_barrier_fault_before_legacy_link(
    bot,
    source_path: Path,
    state_directory: Path,
) -> int:
    """Hard-exit after durable successor commit but before its legacy link."""

    marker = Path(bot.AMBIGUOUS_POST_OUTCOME_FILE)
    successor = state_directory / RESTART_BARRIER_BASENAME
    real_link = bot.os.link

    bot.require_remote_write_marker_removal_protocol = lambda: None

    def exit_before_legacy_link(
        source: object,
        destination: object,
        *,
        follow_symlinks: bool = True,
        **kwargs: object,
    ) -> None:
        if (
            Path(source) == successor
            and Path(destination) == marker
        ):
            successor_stat = os.lstat(successor)
            emit(
                {
                    "legacy_marker_present": os.path.lexists(marker),
                    "phase": "new_barrier_fault_before_legacy_link",
                    "receipts_present": receipt_presence(bot),
                    "source_sha256": source_sha256(source_path),
                    "successor_nlink": int(successor_stat.st_nlink),
                    "successor_ordinary": stat.S_ISREG(
                        successor_stat.st_mode
                    ),
                    "successor_present": True,
                }
            )
            os._exit(75)
        real_link(
            source,
            destination,
            follow_symlinks=follow_symlinks,
            **kwargs,
        )

    bot.os.link = exit_before_legacy_link
    bot.ensure_durable_remote_write_safety_marker(NEW_MARKER_VALUE)
    emit(
        {
            "error": "legacy-link fault injection was not reached",
            "phase": "new_barrier_fault_before_legacy_link",
            "source_sha256": source_sha256(source_path),
        }
    )
    return 76


def inactive_protocol_legacy_loss(
    bot,
    source_path: Path,
    state_directory: Path,
) -> int:
    """Lose a legacy marker, hard-exit, and rely only on protocol inactivity."""

    marker = Path(bot.AMBIGUOUS_POST_OUTCOME_FILE)
    successor = state_directory / RESTART_BARRIER_BASENAME
    blocked_before_loss = bot.ambiguous_remote_post_is_blocking()
    marker.unlink()
    descriptor = os.open(
        state_directory,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    emit(
        {
            "blocked_before_loss": blocked_before_loss,
            "legacy_marker_present": os.path.lexists(marker),
            "phase": "inactive_protocol_legacy_loss",
            "protocol_active": bot.remote_write_safety_protocol_is_active(),
            "receipts_present": receipt_presence(bot),
            "source_sha256": source_sha256(source_path),
            "successor_present": os.path.lexists(successor),
        }
    )
    os._exit(77)


def exercise_direct_preflights(
    bot,
    state_directory: Path,
) -> tuple[
    dict[str, str],
    list[str],
]:
    """Exercise real high-level preflights with local transport sentinels."""

    transport_calls: list[str] = []

    def local_request(*_args: object, **_kwargs: object) -> object:
        transport_calls.append("requests.request")
        raise LocalTransportBoundary

    def local_post(*_args: object, **_kwargs: object) -> object:
        transport_calls.append("requests.post")
        raise LocalTransportBoundary

    bot.requests.request = local_request
    bot.requests.post = local_post
    bot.require_instance_lock_for_remote_write = lambda _operation: None
    bot.global_remote_writes_paused = lambda: False

    image = state_directory / "synthetic-image.jpg"
    image.write_bytes(b"offline synthetic image")

    def clear_untransmitted_fixture_barriers(*paths: Path) -> None:
        """Retire only barriers whose local sentinel proves no request left."""

        for path in paths:
            path.unlink(missing_ok=True)
            descriptor = os.open(
                state_directory,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    checks: tuple[tuple[str, Callable[[], object]], ...] = (
        ("shared_barrier", bot.block_if_ambiguous_remote_post),
        (
            "remote_operation_preflight",
            lambda: bot.require_remote_operation_unpaused(
                "literal-process synthetic operation"
            ),
        ),
        (
            "x_request",
            lambda: bot.x_request(
                "POST",
                "/2/tweets",
                json={"text": "offline synthetic post"},
            ),
        ),
        (
            "x_bearer_request",
            lambda: bot.x_bearer_request(
                "POST",
                "/2/tweets",
                json={"text": "offline synthetic bearer post"},
            ),
        ),
        (
            "create_post",
            lambda: bot.create_post("offline synthetic post"),
        ),
        (
            "provider_request",
            lambda: bot.openai_responses_reply_call(
                request={},
                timeout_seconds=1,
                lane="mention",
                target_id="literal_second_restart",
            ),
        ),
        (
            "media_upload",
            lambda: bot.upload_media(str(image), lane="quote_image"),
        ),
    )
    results: dict[str, str] = {}
    for label, check in checks:
        try:
            check()
        except bot.AmbiguousRemotePostOutcome:
            results[label] = "blocked"
        except LocalTransportBoundary:
            results[label] = "local_transport_reached"
            if label == "media_upload":
                clear_untransmitted_fixture_barriers(
                    Path(bot.MEDIA_UPLOAD_RECEIPT_FILE),
                    Path(str(bot.MEDIA_UPLOAD_RECEIPT_FILE) + ".fence.json"),
                )
        except BaseException as exc:
            results[label] = f"unexpected:{type(exc).__name__}"
        else:
            results[label] = "returned"
    return results, transport_calls


def configure_main_probe(bot, state_directory: Path) -> tuple[list[str], Callable[[], int]]:
    """Configure the real daemon loop around local scheduler-entry sentinels."""

    quote_file = state_directory / "quotes.txt"
    quote_file.write_text("Offline synthetic quote.\n", encoding="utf-8")
    bot.LINES_FILE = quote_file
    bot._PRODUCTION_BOOTSTRAPPED = True
    bot.require_production_bootstrap = lambda: None
    bot.require_established_installation = lambda: None
    bot.acquire_instance_lock = lambda: None
    bot.reconcile_runtime_historical_context_state = lambda: None
    bot.glob = lambda _pattern: []
    bot.ENABLE_DAILY_MEME_POSTS = True
    bot.list_meme_candidates = lambda: []
    bot.validate_original_editorial_shadow_startup = lambda: None
    bot.load_quote_used_hashes = lambda _lines: set()
    bot.load_image_used_basenames = lambda _paths: set()
    bot.current_image_paths = lambda: []
    state = {
        "last_quote_post_epoch": 0,
        "next_meme_post_epoch": 1,
        "next_quote_post_epoch": 1,
    }
    bot.load_runtime_state = lambda: state
    bot.reconcile_startup_main_post_receipts = lambda *_args: None
    bot._tweet_lookup_cache.TweetLookupCache.seed_recent_own_posts = (
        lambda _owner, _state: None
    )
    bot.save_state = lambda _state, **_kwargs: None
    bot.now_epoch = lambda: 1_800_000_000
    bot.global_remote_writes_paused = lambda: False
    bot.scheduler_epoch_from_state = (
        lambda state_arg, key, current: (
            int(state_arg.get(key, current)),
            False,
        )
    )
    bot._daily_meme.MemeSchedule.ensure_initialized = (
        lambda _schedule, _state: None
    )
    bot.ensure_meme_schedule_initialized = lambda _state: (_ for _ in ()).throw(
        AssertionError("main loop returned through root meme-schedule relay")
    )
    bot.lane_paused = lambda _lane: False
    bot.in_api_cooldown = lambda _state, *, scope: False

    entries: list[str] = []

    def historical_context(*_args: object, **_kwargs: object) -> list[object]:
        entries.append("historical_context")
        return []

    def reply_lane(
        _state: dict,
        _current: int,
    ) -> tuple[int, int]:
        entries.append("reply")
        return 0, 0

    def quote_lane(*_args: object, **_kwargs: object) -> None:
        entries.append("quote")

    def meme_lane(*_args: object, **_kwargs: object) -> None:
        entries.append("meme")

    bot.safely_process_due_historical_context_obligations = historical_context
    bot.run_reply_lane_checks_for_tick = reply_lane
    bot.post_random_quote = quote_lane
    bot.post_next_meme = meme_lane
    bot.create_post = lambda *_args, **_kwargs: entries.append("create_post")
    bot.upload_media = lambda *_args, **_kwargs: entries.append("media")
    bot.x_request = lambda *_args, **_kwargs: entries.append("x")
    bot.openai_responses_reply_call = (
        lambda *_args, **_kwargs: entries.append("provider")
    )

    sleep_calls = 0

    def run_for_three_sleeps() -> int:
        nonlocal sleep_calls

        def bounded_sleep(_seconds: float) -> None:
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls == 3:
                raise SchedulerTicksComplete

        bot.sleep = bounded_sleep
        try:
            bot.main()
        except SchedulerTicksComplete:
            pass
        except Exception as exc:
            # A valid unresolved historical-context sending receipt is allowed
            # to stop startup by raising its dedicated ambiguity exception
            # before the scheduler's first sleep.  Preserve that observable
            # outcome instead of losing the literal-process evidence record.
            if type(exc).__name__ != "AmbiguousContextReplyOutcome":
                raise
            run_for_three_sleeps.blocked_exception = type(exc).__name__
        return sleep_calls

    return entries, run_for_three_sleeps


def inspect_process(
    bot,
    source_path: Path,
    state_directory: Path,
    *,
    clean: bool,
) -> int:
    """Inspect direct preflight and real scheduler behaviour in a new process."""

    marker = Path(bot.AMBIGUOUS_POST_OUTCOME_FILE)
    successor = state_directory / RESTART_BARRIER_BASENAME
    initial = {
        "durability_uncertain": bool(
            bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN
        ),
        "marker_present": os.path.lexists(marker),
        "protocol_active": bot.remote_write_safety_protocol_is_active(),
        "receipts_present": receipt_presence(bot),
        "remote_seen": bool(bot._AMBIGUOUS_REMOTE_POST_SEEN),
        "successor_present": os.path.lexists(successor),
    }
    blocking_before_direct = bool(bot.ambiguous_remote_post_is_blocking())
    direct_results, transport_calls = exercise_direct_preflights(
        bot,
        state_directory,
    )
    scheduler_entries, run_scheduler = configure_main_probe(bot, state_directory)
    sleep_calls = run_scheduler()
    result = {
        "blocking_after_scheduler": bool(
            bot.ambiguous_remote_post_is_blocking()
        ),
        "blocking_before_direct": blocking_before_direct,
        "direct_results": direct_results,
        "initial": initial,
        "phase": "clean_process" if clean else "blocked_second_process",
        "scheduler_entries": scheduler_entries,
        "scheduler_sleep_calls": sleep_calls,
        "source_sha256": source_sha256(source_path),
        "transport_sentinel_calls": transport_calls,
    }
    blocked_exception = getattr(run_scheduler, "blocked_exception", None)
    if blocked_exception is not None:
        result["scheduler_blocked_exception"] = blocked_exception
    emit(result)
    return 0


def inspect_receipt_backed_process(
    bot,
    source_path: Path,
    state_directory: Path,
) -> int:
    """Prove the real scheduler and high-level writers stop on the receipt."""

    transport_calls: list[str] = []

    def local_request(*_args: object, **_kwargs: object) -> object:
        transport_calls.append("transport")
        raise LocalTransportBoundary

    bot.requests.request = local_request
    bot.requests.post = local_request
    bot.require_instance_lock_for_remote_write = lambda _operation: None
    bot.global_remote_writes_paused = lambda: False
    image = state_directory / "synthetic-image.jpg"
    image.write_bytes(b"offline synthetic image")
    checks = {
        "create_post": lambda: bot.create_post("offline synthetic post"),
        "media_upload": lambda: bot.upload_media(
            str(image),
            lane="quote_image",
        ),
        "shared_barrier": bot.block_if_ambiguous_remote_post,
    }
    results: dict[str, str] = {}
    for label, check in checks.items():
        try:
            check()
        except bot.AmbiguousRemotePostOutcome:
            results[label] = "blocked"
        except LocalTransportBoundary:
            results[label] = "local_transport_reached"
        else:
            results[label] = "returned"

    scheduler_entries, run_scheduler = configure_main_probe(
        bot,
        state_directory,
    )
    sleep_calls = run_scheduler()
    emit(
        {
            "blocking_after_scheduler": bool(
                bot.ambiguous_remote_post_is_blocking()
            ),
            "blocking_before_direct": True,
            "phase": "receipt_backed_second_process",
            "receipts_present": receipt_presence(bot),
            "regular_receipt_status": bot.load_regular_post_receipt()[0],
            "runtime_results": results,
            "scheduler_entries": scheduler_entries,
            "scheduler_sleep_calls": sleep_calls,
            "source_sha256": source_sha256(source_path),
            "transport_sentinel_calls": transport_calls,
        }
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Dispatch one literal-process probe mode."""

    values = list(sys.argv[1:] if argv is None else argv)
    if len(values) != 3:
        raise SystemExit("usage: DRIVER MODE CANDIDATE_ROOT STATE_DIRECTORY")
    mode, root_text, state_text = values
    root = Path(root_text)
    state_directory = Path(state_text)
    bot, source_path = import_candidate(root, state_directory)
    if mode == "fault":
        return fault_and_hard_exit(bot, source_path, state_directory)
    if mode == "new_fault":
        return new_barrier_fault_before_legacy_link(
            bot,
            source_path,
            state_directory,
        )
    if mode == "inactive_legacy_loss":
        return inactive_protocol_legacy_loss(
            bot,
            source_path,
            state_directory,
        )
    if mode == "blocked":
        return inspect_process(
            bot,
            source_path,
            state_directory,
            clean=False,
        )
    if mode == "clean":
        return inspect_process(
            bot,
            source_path,
            state_directory,
            clean=True,
        )
    raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    raise SystemExit(main())

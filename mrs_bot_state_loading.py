"""Load durable state and select recovery candidates through current dependencies.

One explicit root adapter supplies current paths, backup count, reader fences,
exception, JSON/logger and read, validation, recovery, save and summary callbacks
on each call. The original body preserves strict candidate order, normalized
primary/latest equality, pending-identity fallback and durable repair before
returning the original state. Defaults/schema, I/O, normalization and post-load
runtime initialization remain in existing locations. This owner retains no
callbacks, configuration, paths or state and performs no import-time runtime work
or reverse bot import.
"""

from __future__ import annotations

from collections.abc import Callable
from logging import Logger
from pathlib import Path
from types import ModuleType


def load_state(
    *,
    STATE_BACKUP_COUNT: int,
    STATE_FILE: Path,
    STATE_MINIMUM_READER_VERSION: int,
    STATE_PREVIOUS_READER_COMPATIBILITY_FENCES: tuple[dict, ...],
    STATE_READER_COMPATIBILITY_FENCE: dict,
    UnsafeDurableStateNamespace: type[Exception],
    default_state: Callable[..., dict],
    json: ModuleType,
    log: Logger,
    log_event: Callable[..., None],
    log_json_debug: Callable[..., None],
    normalise_state_candidate: Callable[..., dict | None],
    read_stable_owned_json_bytes_no_follow: Callable[..., tuple[bool, bytes | None]],
    require_compatible_state_reader: Callable[..., int],
    save_state: Callable[..., None],
    state_debug_summary: Callable[..., dict[str, object]],
) -> dict:
    """Load, validate, and recover runtime state from durable storage."""
    log.debug("Loading state from %s", STATE_FILE)

    candidates = [STATE_FILE]
    candidates.extend(STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}") for i in range(1, STATE_BACKUP_COUNT + 1))

    existing_candidates = False
    candidate_recoveries: dict[Path, list[dict[str, object]]] = {}

    def emit_candidate_recoveries(candidate: Path) -> None:
        for recovery in candidate_recoveries.get(candidate, []):
            if recovery.get("kind") == "quote_cursor_suppression_pruned":
                log.info(
                    "Pruned %s malformed, expired, or excess quote cursor "
                    "suppression entry or entries while loading %s",
                    recovery.get("discarded_entries"),
                    candidate,
                )
                continue
            reason = recovery.get("reason")
            if reason == "orphaned_pending_candidates":
                log.warning(
                    "Discarding %s uncovered pending mention candidate(s) "
                    "without pagination provenance while loading %s; watermark "
                    "remains unchanged and a reset guard was installed",
                    recovery.get("discarded_candidates"),
                    candidate,
                )
            elif reason == "continuation_token_limit":
                log.warning(
                    "Resetting oversized mention backlog while loading %s; "
                    "watermark remains unchanged and pending candidates were discarded",
                    candidate,
                )
            else:
                log.warning(
                    "Resetting unsafe mention candidate authority while loading %s "
                    "reason=%s; watermark remains unchanged",
                    candidate,
                    reason,
                )
            log_event("mention_backlog_reset", **recovery)

    def persist_candidate_recoveries(candidate: Path, state: dict) -> None:
        """Commit safe state repairs before any post-load provider work."""
        if not candidate_recoveries.get(candidate):
            return
        save_state(state, durable=True)

    def load_candidate(
        candidate: Path,
        *,
        reject_legacy: bool,
        recover_pending_identity: bool = False,
    ) -> dict | None:
        nonlocal existing_candidates
        try:
            present, data = read_stable_owned_json_bytes_no_follow(candidate)
        except UnsafeDurableStateNamespace:
            existing_candidates = True
            log.critical(
                "State candidate namespace is unsafe; refusing backup fallback: %s",
                candidate,
                exc_info=True,
            )
            raise
        if not present or data is None:
            log.warning("State file candidate does not exist: %s", candidate)
            return None
        existing_candidates = True

        try:
            state = json.loads(data.decode("utf-8"))
        except Exception:
            log.exception("Failed loading state candidate %s", candidate)
            return None

        if not isinstance(state, dict):
            log.error("State file candidate %s is not a JSON object; ignoring", candidate)
            return None
        minimum_reader_version = require_compatible_state_reader(
            state,
            path=candidate,
        )
        legacy_drafts = state.get("pending_reply_drafts")
        if minimum_reader_version >= STATE_MINIMUM_READER_VERSION:
            if legacy_drafts != STATE_READER_COMPATIBILITY_FENCE:
                message = (
                    f"State candidate {candidate} declares minimum reader version "
                    f"{minimum_reader_version} without the exact compatibility "
                    "fence; refusing unsafe rollback state"
                )
                if reject_legacy:
                    log.critical(message)
                    raise RuntimeError(message)
                log.warning(
                    "%s; candidate is not needed because primary state is usable",
                    message,
                )
                return None
        elif (
            legacy_drafts not in (None, {})
            and legacy_drafts not in STATE_PREVIOUS_READER_COMPATIBILITY_FENCES
        ):
            message = (
                f"Legacy V1 reply drafts remain in {candidate}; refusing to interpret or post them "
                "through the AI-first strategy"
            )
            if reject_legacy:
                log.critical(message)
                raise RuntimeError(message)
            log.warning("%s; candidate is not needed because primary state is usable", message)
            return None
        state.pop("pending_reply_drafts", None)
        state["minimum_reader_version"] = max(
            minimum_reader_version,
            STATE_MINIMUM_READER_VERSION,
        )
        recovery_events: list[dict[str, object]] = []
        normalised = normalise_state_candidate(
            state,
            path=candidate,
            recovery_events=recovery_events,
            recover_pending_identity=recover_pending_identity,
        )
        if normalised is not None:
            candidate_recoveries[candidate] = recovery_events
        return normalised

    latest_backup_path = STATE_FILE.with_name(f"{STATE_FILE.name}.bak1")
    primary = load_candidate(STATE_FILE, reject_legacy=True)
    if primary is not None:
        latest_backup = (
            load_candidate(latest_backup_path, reject_legacy=False)
            if STATE_BACKUP_COUNT > 0
            else None
        )
        if latest_backup is not None and latest_backup != primary:
            message = (
                "Primary state and latest committed backup are both valid but "
                "diverge; refusing to guess which durable generation is newer"
            )
            log.critical(
                "%s primary=%s backup=%s",
                message,
                STATE_FILE,
                latest_backup_path,
            )
            raise RuntimeError(message)
        emit_candidate_recoveries(STATE_FILE)
        persist_candidate_recoveries(STATE_FILE, primary)
        log_json_debug("Loaded state summary", state_debug_summary(primary))
        return primary

    # Only inspect backups until the first usable generation is found.  Older
    # snapshots are recovery fallbacks, not vetoes over a newer usable state.
    for candidate in candidates[1:]:
        recovered = load_candidate(candidate, reject_legacy=True)
        if recovered is None:
            continue
        log.warning("Recovered state from backup %s", candidate)
        emit_candidate_recoveries(candidate)
        persist_candidate_recoveries(candidate, recovered)
        log_json_debug("Loaded state summary", state_debug_summary(recovered))
        return recovered

    if existing_candidates:
        # Pending identity corruption is recoverable only after every strict
        # candidate has failed, so a usable backup always remains authoritative.
        for candidate in candidates:
            recovered = load_candidate(
                candidate,
                reject_legacy=True,
                recover_pending_identity=True,
            )
            if recovered is None:
                continue
            log.warning(
                "Recovered state candidate %s by discarding corrupt pending "
                "mention identity and requiring a head refetch",
                candidate,
            )
            emit_candidate_recoveries(candidate)
            persist_candidate_recoveries(candidate, recovered)
            log_json_debug("Loaded state summary", state_debug_summary(recovered))
            return recovered
        message = "Existing state file(s) found but no usable state or backup; refusing to start with empty state"
        log.critical(message)
        raise RuntimeError(message)

    log.error("No state file or backup found; using default state")
    return default_state()

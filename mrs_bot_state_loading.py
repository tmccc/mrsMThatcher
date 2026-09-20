"""Select provable durable state generations and migrate unambiguous legacy state.

Sealed sequence numbers order complete replicas; conflicting identities fail
closed. Selected-candidate recovery reporting is separate from replica selection;
repairs and legacy migration publish through the locked state writer. Rollback
fence grammar is checked before normalization, with strict/permissive candidate
handling retained at the loader boundary.
Reader policy and filesystem dependencies are supplied explicitly by the root;
this module retains no runtime authority or import-time side effects."""

from __future__ import annotations

from collections.abc import Callable
from logging import Logger
from pathlib import Path

from mrs_bot_observability import state_debug_summary


def _select_latest_generation(
    candidates: dict[Path, dict],
    generations: dict[Path, tuple[int, bytes]],
) -> Path:
    """Select the newest replica only after every sequence has one exact identity."""
    identities: dict[int, bytes] = {}
    for candidate in candidates:
        sequence, encoded = generations[candidate]
        if sequence in identities and identities[sequence] != encoded:
            raise RuntimeError('conflicting state generation identities; refusing to guess')
        identities[sequence] = encoded
    return max(candidates, key=lambda path: generations[path][0])


def _emit_candidate_recoveries(
    candidate: Path,
    recoveries: list[dict[str, object]],
    *,
    log: Logger,
    log_event: Callable[..., None],
) -> None:
    """Report only the selected candidate's repairs before its durable write."""
    for recovery in recoveries:
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


def _rollback_fence_error(
    legacy_drafts: object,
    minimum_reader_version: int,
    *,
    candidate: Path,
    required_reader_version: int,
    current_fence: dict,
    previous_fences: tuple[dict, ...],
) -> str | None:
    """Describe incompatible rollback data before candidate normalization."""
    if minimum_reader_version >= required_reader_version:
        if legacy_drafts != current_fence:
            return (
                f"State candidate {candidate} declares minimum reader version "
                f"{minimum_reader_version} without the exact compatibility "
                "fence; refusing unsafe rollback state"
            )
    elif legacy_drafts not in (None, {}) and legacy_drafts not in previous_fences:
        return (
            f"Legacy V1 reply drafts remain in {candidate}; refusing to interpret or post them "
            "through the AI-first strategy"
        )
    return None


def load_state(
    *,
    STATE_BACKUP_COUNT: int,
    STATE_FILE: Path,
    STATE_MINIMUM_READER_VERSION: int,
    STATE_PREVIOUS_READER_COMPATIBILITY_FENCES: tuple[dict, ...],
    STATE_READER_COMPATIBILITY_FENCE: dict,
    UnsafeDurableStateNamespace: type[Exception],
    default_state: Callable[..., dict],
    log: Logger,
    log_event: Callable[..., None],
    log_json_debug: Callable[..., None],
    normalise_state_candidate: Callable[..., dict | None],
    read_stable_owned_json_bytes_no_follow: Callable[..., tuple[bool, bytes | None]],
    require_compatible_state_reader: Callable[..., int],
    save_state: Callable[..., None],
) -> dict:
    """Load, validate, and recover runtime state from durable storage."""
    from mrs_bot_state_generation import (
        canonical_bytes, generation_number, require_unambiguous_legacy_documents,
        strict_document,
    )

    log.debug("Loading state from %s", STATE_FILE)

    candidates = [STATE_FILE]
    candidates.extend(STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}") for i in range(1, STATE_BACKUP_COUNT + 1))

    existing_candidates = False
    candidate_recoveries: dict[Path, list[dict[str, object]]] = {}
    candidate_generations: dict[Path, tuple[int, bytes]] = {}

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
            state = strict_document(data)
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
        fence_error = _rollback_fence_error(
            legacy_drafts,
            minimum_reader_version,
            candidate=candidate,
            required_reader_version=STATE_MINIMUM_READER_VERSION,
            current_fence=STATE_READER_COMPATIBILITY_FENCE,
            previous_fences=STATE_PREVIOUS_READER_COMPATIBILITY_FENCES,
        )
        if fence_error is not None:
            if reject_legacy:
                log.critical(fence_error)
                raise RuntimeError(fence_error)
            log.warning(
                "%s; candidate is not needed because primary state is usable",
                fence_error,
            )
            return None
        try:
            sequence = generation_number(state)
        except ValueError:
            log.exception("Invalid sealed state generation %s", candidate)
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
            candidate_generations[candidate] = (sequence, canonical_bytes(strict_document(data)))
        return normalised

    latest_backup_path = STATE_FILE.with_name(f"{STATE_FILE.name}.bak1")
    primary = load_candidate(STATE_FILE, reject_legacy=True)
    # Generation-aware files carry ordering in their own atomic document. Scan
    # all replicas and fail on sequence collisions, rather than treating a stale
    # latest backup as a veto over a committed primary.
    modern: dict[Path, dict] = {}
    if primary is not None and candidate_generations[STATE_FILE][0]:
        modern[STATE_FILE] = primary
    inspected_backups: dict[Path, dict | None] = {}
    for candidate in candidates[1:]:
        recovered = load_candidate(candidate, reject_legacy=primary is None)
        inspected_backups[candidate] = recovered
        if recovered is not None and candidate_generations[candidate][0]:
            modern[candidate] = recovered
    if modern:
        selected = _select_latest_generation(modern, candidate_generations)
        recovered = modern[selected]
        sequence, encoded = candidate_generations[selected]
        repair = selected != STATE_FILE or bool(candidate_recoveries.get(selected))
        if STATE_BACKUP_COUNT:
            repair = repair or candidate_generations.get(latest_backup_path) != (sequence, encoded)
        _emit_candidate_recoveries(
            selected, candidate_recoveries.get(selected, []), log=log, log_event=log_event,
        )
        if repair:
            try:
                save_state(recovered, durable=True)
            except Exception as exc:
                # The backup can remain unavailable without invalidating a
                # newly fsynced canonical generation. No other error qualifies.
                proof = getattr(exc, 'commit_proof', None)
                if proof is None:
                    raise
                proof.require_current()
        log_json_debug('Loaded state summary', state_debug_summary(recovered))
        return recovered
    legacy_candidates = {
        candidate: recovered for candidate, recovered in inspected_backups.items()
        if recovered is not None
    }
    if primary is not None:
        legacy_candidates[STATE_FILE] = primary
    require_unambiguous_legacy_documents(legacy_candidates, STATE_FILE)
    if primary is not None:
        _emit_candidate_recoveries(
            STATE_FILE, candidate_recoveries.get(STATE_FILE, []), log=log, log_event=log_event,
        )
        # Migration occurs only after legacy equality is established; save_state
        # proves the instance/state lock before publishing the first generation.
        save_state(primary, durable=True)
        log_json_debug("Loaded state summary", state_debug_summary(primary))
        return primary

    # All usable legacy fallbacks now agree; pathname order is no longer used
    # to infer freshness between divergent documents.
    for candidate in candidates[1:]:
        recovered = inspected_backups.get(candidate)
        if recovered is None:
            continue
        log.warning("Recovered state from backup %s", candidate)
        _emit_candidate_recoveries(
            candidate, candidate_recoveries.get(candidate, []), log=log, log_event=log_event,
        )
        save_state(recovered, durable=True)
        log_json_debug("Loaded state summary", state_debug_summary(recovered))
        return recovered

    if existing_candidates:
        # Pending identity corruption is recoverable only after every strict
        # candidate has failed, so a usable backup always remains authoritative.
        repairable_candidates = {}
        for candidate in candidates:
            recovered = load_candidate(
                candidate,
                reject_legacy=True,
                recover_pending_identity=True,
            )
            if recovered is None:
                continue
            repairable_candidates[candidate] = recovered
        modern_repairable = {
            candidate: recovered for candidate, recovered in repairable_candidates.items()
            if candidate_generations[candidate][0]
        }
        if modern_repairable:
            candidate = _select_latest_generation(modern_repairable, candidate_generations)
            recovered = modern_repairable[candidate]
        else:
            require_unambiguous_legacy_documents(repairable_candidates, STATE_FILE)
            candidate = next(iter(repairable_candidates), None)
            recovered = repairable_candidates.get(candidate)
        if candidate is not None and recovered is not None:
            log.warning(
                "Recovered state candidate %s by discarding corrupt pending "
                "mention identity and requiring a head refetch",
                candidate,
            )
            _emit_candidate_recoveries(
                candidate, candidate_recoveries.get(candidate, []), log=log, log_event=log_event,
            )
            persist_candidate_recoveries(candidate, recovered)
            log_json_debug("Loaded state summary", state_debug_summary(recovered))
            return recovered
        message = "Existing state file(s) found but no usable state or backup; refusing to start with empty state"
        log.critical(message)
        raise RuntimeError(message)

    log.error("No state file or backup found; using default state")
    return default_state()

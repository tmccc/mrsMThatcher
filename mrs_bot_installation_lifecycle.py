"""Installation setup, establishment and startup ledger recovery.

The root supplies current runtime dependencies explicitly on each call. This
module performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

from typing import Any


def required_installation_files_missing(
    *,
    HISTORICAL_CONTEXT_REPLY_HISTORY_FILE: Any,
    HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE: Any,
    IMAGES_USED_FILE: Any,
    INSTALLATION_IN_PROGRESS_FILE: Any,
    LINES_USED_FILE: Any,
    STATE_BACKUP_COUNT: Any,
    STATE_FILE: Any,
    durable_state_namespace_is_owned_single_link_file: Any,
    os: Any,
    remote_source_receipt_paths: Any,
    retirement_ledger_is_blocking: Any,
    retirement_ledger_paths: Any,
) -> list[Path]:
    """Return durable files that cannot be recovered from local backups."""
    missing = [
        path
        for path in (
            LINES_USED_FILE,
            IMAGES_USED_FILE,
        )
        if not durable_state_namespace_is_owned_single_link_file(path)
    ]
    # The history and outbox stores apply their own schema-specific read
    # limits. Establishment checks only their shared namespace contract here;
    # imposing the smaller core-state limit would reject a valid store before
    # its authoritative loader could inspect it.
    missing.extend(
        path
        for path in (
            HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
            HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE,
        )
        if not durable_state_namespace_is_owned_single_link_file(
            path,
            maximum_bytes=None,
        )
    )
    for receipt_path in remote_source_receipt_paths():
        if retirement_ledger_is_blocking(receipt_path):
            ledger_path, _exchange_path = retirement_ledger_paths(receipt_path)
            missing.append(ledger_path)
    # Installations created before the explicit marker protocol remain valid
    # when all established durable files are present.  A new initializer
    # publishes this separate sentinel before its first data write, so any
    # interrupted new installation remains fail closed without rejecting the
    # already deployed legacy installation.
    try:
        os.lstat(INSTALLATION_IN_PROGRESS_FILE)
    except FileNotFoundError:
        pass
    except OSError:
        missing.append(INSTALLATION_IN_PROGRESS_FILE)
    else:
        missing.append(INSTALLATION_IN_PROGRESS_FILE)
    state_candidates = [STATE_FILE]
    state_candidates.extend(
        STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}")
        for i in range(1, STATE_BACKUP_COUNT + 1)
    )
    for candidate in state_candidates:
        try:
            os.lstat(candidate)
        except FileNotFoundError:
            continue
        except OSError:
            missing.append(candidate)
            continue
        if not durable_state_namespace_is_owned_single_link_file(candidate):
            missing.append(candidate)
    if not any(
        durable_state_namespace_is_owned_single_link_file(path)
        for path in state_candidates
    ):
        missing.append(STATE_FILE)
    return list(dict.fromkeys(missing))


def require_established_installation(
    *,
    required_installation_files_missing: Any,
) -> None:
    """Refuse operational startup after unexpected durable-state loss."""
    missing = required_installation_files_missing()
    if missing:
        raise RuntimeError(
            "Required durable production state/history is missing: "
            + ", ".join(str(path) for path in missing)
            + ". Restore the files or use --initialise only for a genuinely new installation."
        )


def recover_interrupted_retirement_ledger_exchanges_at_startup(
    *,
    ProtocolActivationError: Any,
    REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE: Any,
    inspect_protocol_activation: Any,
    inspect_retirement_ledger: Any,
    recover_retirement_ledger_exchange_if_present: Any,
    remote_source_receipt_paths: Any,
    transaction_mutation_authority: Any,
) -> tuple[Path, ...]:
    """Finish exact ledger exchanges before installation completeness checks.

    A completed ledger transition can leave its old generation in the fixed
    exchange pathname if the process dies between the atomic exchange and its
    final cleanup.  That pathname must block every remote preflight, but under
    the already-held singleton lock a current ledger-aware activation may
    deterministically finish the exact predecessor/successor exchange.  No
    missing, malformed, ambiguous, or pre-ledger installation is repaired
    here.
    """

    inspections = {
        receipt_path: inspect_retirement_ledger(receipt_path)
        for receipt_path in remote_source_receipt_paths()
    }
    recoverable = tuple(
        receipt_path
        for receipt_path, inspection in inspections.items()
        if inspection.valid
        and inspection.blocking
        and inspection.state in {"exchange_staged", "exchange_committed"}
    )
    if not recoverable:
        return ()
    try:
        inspect_protocol_activation(
            REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE
        )
    except (ProtocolActivationError, OSError):
        # Installation validation below remains fail closed.  An absent or
        # invalid permission generation cannot authorise namespace mutation.
        return ()
    authority = transaction_mutation_authority(
        "startup retirement-ledger atomic exchange recovery"
    )
    recovered: list[Path] = []
    for receipt_path in recoverable:
        if recover_retirement_ledger_exchange_if_present(
            receipt_path,
            mutation_authority=authority,
        ):
            recovered.append(receipt_path)
    return tuple(recovered)


def require_established_installation_after_ledger_recovery(
    *,
    log: Any,
    recover_interrupted_retirement_ledger_exchanges_at_startup: Any,
    require_established_installation: Any,
) -> None:
    """Recover exact ledger exchanges, then require a complete installation."""

    recovered = recover_interrupted_retirement_ledger_exchanges_at_startup()
    if recovered:
        log.warning(
            "Recovered crash-left permanent retirement-ledger exchanges "
            "before installation validation: %s",
            [str(path) for path in recovered],
        )
    require_established_installation()


def initialise_installation(
    *,
    AMBIGUOUS_POST_OUTCOME_FILE: Any,
    AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE: Any,
    BASE_DIR: Any,
    CONFIRMED_REPLY_RECEIPT_FILE: Any,
    ENABLE_DAILY_MEME_POSTS: Any,
    HISTORICAL_CONTEXT_REPLY_HISTORY_FILE: Any,
    HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE: Any,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    IMAGES_USED_FILE: Any,
    INSTALLATION_IN_PROGRESS_FILE: Any,
    INSTALLATION_MARKER_FILE: Any,
    JOURNAL_RETIREMENT_PREFIX: Any,
    JOURNAL_STAGING_PREFIX: Any,
    LINES_USED_FILE: Any,
    MEDIA_RETIREMENT_GUARD_PREFIX: Any,
    MEDIA_TRANSITION_PREFIX: Any,
    MEDIA_UPLOAD_RECEIPT_FILE: Any,
    MEME_POST_RECEIPT_FILE: Any,
    POST_SLEEP_MIN: Any,
    REGULAR_POST_RECEIPT_FILE: Any,
    REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_AUDIT_BASENAME: Any,
    REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE: Any,
    STATE_BACKUP_COUNT: Any,
    STATE_FILE: Any,
    acquire_instance_lock: Any,
    atomic_write_json: Any,
    default_state: Any,
    ensure_meme_schedule_initialized: Any,
    fence_path_for_journal: Any,
    fsync_parent_dir: Any,
    historical_context_outbox_store: Any,
    historical_context_reply_store: Any,
    initialise_retirement_ledger: Any,
    journal_path_for_receipt: Any,
    log: Any,
    media_fence_path_for_receipt: Any,
    now_epoch: Any,
    os: Any,
    remote_source_receipt_paths: Any,
    require_production_bootstrap: Any,
    retirement_auxiliary_paths: Any,
    retirement_ledger_paths: Any,
    save_image_used_basenames: Any,
    save_quote_used_hashes: Any,
    save_state: Any,
    transaction_mutation_authority: Any,
) -> int:
    """Create a new state/history set without starting production."""
    from historical_context_formatter import HistoricalContextReplyStore

    require_production_bootstrap()
    # This one-shot command mutates the daemon's durable namespace.  Own the
    # ordinary process/state-directory lock before proving that namespace
    # empty so a running daemon or concurrent initializer cannot race the
    # inspection/write interval.  The command exits immediately afterwards
    # and deliberately retains the lock until process exit.
    acquire_instance_lock()
    candidates = [
        INSTALLATION_MARKER_FILE,
        INSTALLATION_IN_PROGRESS_FILE,
        STATE_FILE,
        STATE_FILE.with_suffix(".tmp"),
        LINES_USED_FILE,
        LINES_USED_FILE.with_suffix(f"{LINES_USED_FILE.suffix}.tmp"),
        IMAGES_USED_FILE,
        IMAGES_USED_FILE.with_suffix(f"{IMAGES_USED_FILE.suffix}.tmp"),
    ]
    candidates.extend(STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}") for i in range(1, STATE_BACKUP_COUNT + 1))
    candidates.extend(
        STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}.tmp")
        for i in range(1, STATE_BACKUP_COUNT + 1)
    )
    candidates.extend(
        (
            REGULAR_POST_RECEIPT_FILE,
            MEME_POST_RECEIPT_FILE,
            CONFIRMED_REPLY_RECEIPT_FILE,
            journal_path_for_receipt(REGULAR_POST_RECEIPT_FILE),
            fence_path_for_journal(
                journal_path_for_receipt(REGULAR_POST_RECEIPT_FILE)
            ),
            MEDIA_UPLOAD_RECEIPT_FILE,
            media_fence_path_for_receipt(MEDIA_UPLOAD_RECEIPT_FILE),
            AMBIGUOUS_POST_OUTCOME_FILE,
            AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
            REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE.with_name(
                REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_AUDIT_BASENAME
            ),
            REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE,
            HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
            HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
            HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE,
            HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.with_name(
                f"{HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.name}.lock"
            ),
            HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.with_name(
                f"{HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.name}.worker.lock"
            ),
        )
    )
    candidates.extend(
        auxiliary
        for receipt_path in remote_source_receipt_paths()
        for auxiliary in retirement_auxiliary_paths(receipt_path)
    )
    candidates.extend(
        ledger_path
        for receipt_path in remote_source_receipt_paths()
        for ledger_path in retirement_ledger_paths(receipt_path)
    )
    # Transition/retirement auxiliaries carry random identity suffixes. Inspect
    # their exact reserved lexical prefixes without following entries rather
    # than guessing names or treating an orphan as a fresh installation.
    try:
        with os.scandir(BASE_DIR) as entries:
            candidates.extend(
                BASE_DIR / entry.name
                for entry in entries
                if entry.name.startswith(
                    (
                        JOURNAL_STAGING_PREFIX,
                        JOURNAL_RETIREMENT_PREFIX,
                        MEDIA_TRANSITION_PREFIX,
                        MEDIA_RETIREMENT_GUARD_PREFIX,
                    )
                )
            )
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RuntimeError(
            "Refusing to initialise because the state directory namespace "
            "cannot be inventoried"
        ) from exc
    existing: list[Path] = []
    for path in candidates:
        try:
            os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RuntimeError(
                "Refusing to initialise because an existing-state namespace "
                f"entry cannot be inspected: {path}"
            ) from exc
        existing.append(path)
    if existing:
        raise RuntimeError(
            "Refusing to initialise over an existing or partially established installation: "
            + ", ".join(str(path) for path in existing)
        )

    BASE_DIR.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        current = now_epoch()
        created.append(INSTALLATION_IN_PROGRESS_FILE)
        atomic_write_json(
            INSTALLATION_IN_PROGRESS_FILE,
            {
                "schema_version": 1,
                "state": "initialising",
                "started_at_epoch": current,
            },
            durable=True,
        )
        # Register every state pathname before schedule initialisation: that
        # helper may persist state when daily memes are enabled.
        created.append(STATE_FILE)
        created.append(STATE_FILE.with_suffix(".tmp"))
        created.extend(
            STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}")
            for i in range(1, STATE_BACKUP_COUNT + 1)
        )
        created.extend(
            STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}.tmp")
            for i in range(1, STATE_BACKUP_COUNT + 1)
        )
        state = default_state()
        state["next_quote_post_epoch"] = current + POST_SLEEP_MIN
        if ENABLE_DAILY_MEME_POSTS:
            ensure_meme_schedule_initialized(state)
        save_state(state, durable=True)
        created.append(LINES_USED_FILE)
        created.append(
            LINES_USED_FILE.with_suffix(f"{LINES_USED_FILE.suffix}.tmp")
        )
        save_quote_used_hashes(LINES_USED_FILE, set(), durable=True)
        created.append(IMAGES_USED_FILE)
        created.append(
            IMAGES_USED_FILE.with_suffix(f"{IMAGES_USED_FILE.suffix}.tmp")
        )
        save_image_used_basenames(IMAGES_USED_FILE, set(), durable=True)
        context_store = historical_context_reply_store(
            allow_missing_history=True
        )
        created.append(context_store.history_path)
        context_store.initialise_empty_history()
        outbox = historical_context_outbox_store()
        created.extend((outbox.path, outbox.lock_path))
        outbox.initialise_empty()
        ledger_authority = transaction_mutation_authority(
            "new-install retirement-ledger initialisation"
        )
        for receipt_path in remote_source_receipt_paths():
            ledger_path, exchange_path = retirement_ledger_paths(receipt_path)
            created.extend((ledger_path, exchange_path))
            inspection = initialise_retirement_ledger(
                receipt_path,
                mutation_authority=ledger_authority,
            )
            if (
                not inspection.valid
                or inspection.blocking
                or inspection.state != "idle"
                or inspection.sequence != 0
            ):
                raise RuntimeError(
                    "New-install retirement ledger did not reach its exact "
                    f"genesis state: {receipt_path}"
                )
        created.append(INSTALLATION_MARKER_FILE)
        atomic_write_json(
            INSTALLATION_MARKER_FILE,
            {"schema_version": 1, "initialised_at_epoch": current},
            durable=True,
        )
        INSTALLATION_IN_PROGRESS_FILE.unlink()
        fsync_parent_dir(INSTALLATION_IN_PROGRESS_FILE, strict=True)
    except Exception as initialisation_error:
        cleanup_failures: list[str] = []
        for path in reversed(created):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                # Never recurse through, replace, or follow an unexpected
                # namespace object.  Continue removing the remaining files,
                # preserve the initiating failure, and leave the installation
                # completeness checks to keep any residue non-operational.
                cleanup_failures.append(
                    f"{path}: {type(cleanup_error).__name__}: {cleanup_error}"
                )
        if cleanup_failures:
            # Python 3.10 has no BaseException.add_note().  Attach structured
            # diagnostics without replacing the initiating exception and emit
            # the same information to the local log.
            initialisation_error.initialisation_cleanup_failures = tuple(
                cleanup_failures
            )
            log.error(
                "Initialisation rollback left exact non-file or unremovable "
                "paths: %s",
                "; ".join(cleanup_failures),
            )
        raise
    print(
        f"Initialised durable MrsMThatcher state in {BASE_DIR}; production was "
        "not started. Remote writes remain disabled until the stopped "
        "external-attestation protocol activator succeeds."
    )
    return 0

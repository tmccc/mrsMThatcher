"""Publish durable state and backup generations through current root dependencies.

Five root adapters supply current paths, counts, compatibility constants, modules,
error classes, security and logging helpers, and nested callbacks on each call.
Original bodies retain shallow state versus deep fence copies, exact source bytes,
optional fsync, reverse backup rotation, and canonical commit before latest backup.
Source/receipt I/O, state loading, validation and persistence callers remain in
existing locations. Explicit calls prepare documents or write supplied paths;
this owner retains no callbacks, configuration, paths, state or descriptors and
performs no import-time runtime work or reverse application import.
"""

from __future__ import annotations

from collections.abc import Callable
from logging import Logger
from pathlib import Path
from types import ModuleType


def state_document_for_persistence(
    state: dict,
    *,
    STATE_FILE: Path,
    STATE_MINIMUM_READER_VERSION: int,
    STATE_PREVIOUS_READER_COMPATIBILITY_FENCES: tuple[dict, ...],
    STATE_READER_COMPATIBILITY_FENCE: dict,
    copy: ModuleType,
    require_compatible_state_reader: Callable[..., int],
) -> dict:
    """Return state with the reader declaration and pre-reader rollback fence."""
    minimum = require_compatible_state_reader(state, path=STATE_FILE)
    legacy_drafts = state.get("pending_reply_drafts")
    if legacy_drafts not in (
        None,
        {},
        STATE_READER_COMPATIBILITY_FENCE,
        *STATE_PREVIOUS_READER_COMPATIBILITY_FENCES,
    ):
        raise RuntimeError(
            "Legacy V1 reply drafts remain in runtime state; refusing to "
            "overwrite them with the reader compatibility fence"
        )
    document = dict(state)
    document["minimum_reader_version"] = max(
        minimum,
        STATE_MINIMUM_READER_VERSION,
    )
    document["pending_reply_drafts"] = copy.deepcopy(
        STATE_READER_COMPATIBILITY_FENCE
    )
    return document


def copy_state_backup(
    src: Path,
    dst: Path,
    *,
    durable: bool = False,
    Path: type[Path],
    UnsafeDurableStateNamespace: type[Exception],
    fsync_parent_dir: Callable[..., None],
    os: ModuleType,
    read_stable_owned_json_bytes_no_follow: Callable[..., tuple[bool, bytes | None]],
    tempfile: ModuleType,
) -> None:
    """Copy one exact stable state generation without following links."""

    present, data = read_stable_owned_json_bytes_no_follow(src)
    if not present or data is None:
        raise UnsafeDurableStateNamespace(
            f"state backup source disappeared before copying: {src}"
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{dst.name}.",
        dir=dst.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(data)
            if durable:
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(temporary, dst)
        if durable:
            fsync_parent_dir(dst, strict=True)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def rotate_state_backups_before_commit(
    *,
    durable: bool = False,
    STATE_BACKUP_COUNT: int,
    STATE_FILE: Path,
    copy_state_backup: Callable[..., None],
    log: Logger,
) -> None:
    """Rotate state backups before commit."""
    if STATE_BACKUP_COUNT <= 1 or not STATE_FILE.exists():
        return

    try:
        for i in range(STATE_BACKUP_COUNT, 2, -1):
            older = STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i - 1}")
            newer = STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}")
            if older.exists():
                older.replace(newer)

        bak2 = STATE_FILE.with_name(f"{STATE_FILE.name}.bak2")
        copy_state_backup(STATE_FILE, bak2, durable=durable)
        log.debug("Previous state backup written: %s", bak2)
    except Exception:
        log.exception("Failed rotating state backups; continuing with state save")


def write_latest_state_backup(
    *,
    durable: bool = False,
    STATE_BACKUP_COUNT: int,
    STATE_FILE: Path,
    copy_state_backup: Callable[..., None],
    log: Logger,
) -> None:
    """Write latest state backup."""
    if STATE_BACKUP_COUNT <= 0 or not STATE_FILE.exists():
        return
    bak1 = STATE_FILE.with_name(f"{STATE_FILE.name}.bak1")
    copy_state_backup(STATE_FILE, bak1, durable=durable)
    log.debug("Latest committed state backup written: %s", bak1)


def save_state(
    state: dict,
    *,
    durable: bool = False,
    Path: type[Path],
    STATE_FILE: Path,
    StateBackupWriteError: type[Exception],
    fsync_parent_dir: Callable[..., None],
    json: ModuleType,
    log: Logger,
    log_json_debug: Callable[..., None],
    os: ModuleType,
    rotate_state_backups_before_commit: Callable[..., None],
    state_debug_summary: Callable[..., dict[str, object]],
    state_document_for_persistence: Callable[..., dict],
    tempfile: ModuleType,
    test_process_production_state_write_blocked: Callable[..., bool],
    write_latest_state_backup: Callable[..., None],
) -> None:
    """Persist state atomically, logging only a value-free structural summary."""
    if test_process_production_state_write_blocked(STATE_FILE):
        raise RuntimeError(f"Refusing test-process write to production state: {STATE_FILE}")
    log.debug("Saving state to %s", STATE_FILE)
    log_json_debug("State summary being saved", state_debug_summary(state))
    persisted_state = state_document_for_persistence(state)

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{STATE_FILE.name}.",
        dir=STATE_FILE.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(persisted_state, handle, indent=2, sort_keys=True)
            if durable:
                handle.flush()
                os.fsync(handle.fileno())

        rotate_state_backups_before_commit(durable=durable)
        os.replace(temporary, STATE_FILE)
        if durable:
            fsync_parent_dir(STATE_FILE, strict=durable)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    try:
        write_latest_state_backup(durable=durable)
    except Exception as exc:
        raise StateBackupWriteError(
            f"Canonical state committed but latest backup write failed: {STATE_FILE}"
        ) from exc

"""Safety-marker snapshots and durability acknowledgement.

Path values and file-mode interpretation are local. The root supplies current
filesystem and acknowledgement boundaries explicitly on each call. This module
performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

from mrs_bot_durable_json_io import canonical_atomic_json_bytes


def remote_write_safety_marker_path_present_or_unsafe(
    *,
    AMBIGUOUS_POST_OUTCOME_FILE: Any,
    AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE: Any,
    latch_remote_write_safety_marker_observation: Any,
    log: Any,
    os: Any,
) -> bool:
    """Treat either barrier namespace entry or inspection error as blocking.

    ``Path.exists()`` follows symlinks and therefore reports a dangling link as
    absent.  A malformed, replaced, unreadable or otherwise unusual entry is
    not evidence that the remote-write incident has been reconciled.
    """
    found = False
    for path in (
        AMBIGUOUS_POST_OUTCOME_FILE,
        AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
    ):
        try:
            os.lstat(path)
        except FileNotFoundError:
            continue
        except Exception:
            latch_remote_write_safety_marker_observation()
            log.critical(
                "A remote-write safety barrier namespace cannot be inspected; "
                "treating all remote writes as blocked path=%s",
                path,
                exc_info=True,
            )
            return True
        latch_remote_write_safety_marker_observation()
        found = True
    return found


def require_remote_write_marker_removal_protocol(
    *,
    require_instance_lock_for_remote_write: Any,
) -> None:
    """Require the process-lifetime instance lock for marker acknowledgement.

    Production marker removal is supported only while the bot is stopped and a
    reconciler holds ``mrsMThatcher.lock`` exclusively.  The running daemon
    holds that lock for its lifetime, so a cooperating reconciler cannot remove
    the marker after the acknowledgement recheck.  Tests use isolated paths and
    exercise the same byte/identity checks without a production lock.
    """
    require_instance_lock_for_remote_write(
        "Remote-write safety marker acknowledgement"
    )


def read_remote_write_safety_marker_snapshot(
    path: Path | None = None,
    *,
    accepted_link_counts: frozenset[int] = frozenset({1}),
    AMBIGUOUS_POST_OUTCOME_FILE: Any,
    REMOTE_WRITE_SAFETY_MARKER_MAX_BYTES: Any,
    latch_remote_write_safety_marker_observation: Any,
    os: Any,
) -> tuple[int, int, int, int, bytes]:
    """Read one bounded, no-follow marker snapshot with stable file identity."""
    path = AMBIGUOUS_POST_OUTCOME_FILE if path is None else Path(path)
    before = os.lstat(path)
    # A marker pathname is the surviving restart barrier.  Seed both
    # process-local barriers before any later open, read, fsync or revalidation
    # can fail or race with a cooperating filesystem actor.
    latch_remote_write_safety_marker_observation()
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError("Remote-write safety marker is not a regular file")
    if before.st_nlink not in accepted_link_counts:
        raise RuntimeError(
            "Remote-write safety marker has an unsupported filesystem-link count"
        )
    if before.st_size > REMOTE_WRITE_SAFETY_MARKER_MAX_BYTES:
        raise RuntimeError("Remote-write safety marker exceeds the size limit")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise RuntimeError("O_NOFOLLOW is required for safety-marker inspection")
    fd = os.open(path, flags | nofollow)
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != before.st_nlink
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            raise RuntimeError(
                "Remote-write safety marker changed while it was opened"
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(8192, REMOTE_WRITE_SAFETY_MARKER_MAX_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > REMOTE_WRITE_SAFETY_MARKER_MAX_BYTES:
                raise RuntimeError("Remote-write safety marker exceeds the size limit")
        after_read = os.fstat(fd)
        if (
            after_read.st_dev != opened.st_dev
            or after_read.st_ino != opened.st_ino
            or after_read.st_nlink != opened.st_nlink
            or after_read.st_size != opened.st_size
            or after_read.st_ctime_ns != opened.st_ctime_ns
            or after_read.st_mtime_ns != opened.st_mtime_ns
        ):
            raise RuntimeError(
                "Remote-write safety marker changed while it was read"
            )
        os.fsync(fd)
        after_sync = os.fstat(fd)
        after_path = os.lstat(path)
        if (
            not stat.S_ISREG(after_path.st_mode)
            or after_sync.st_nlink != opened.st_nlink
            or after_path.st_nlink != opened.st_nlink
            or after_sync.st_dev != opened.st_dev
            or after_sync.st_ino != opened.st_ino
            or after_sync.st_size != opened.st_size
            or after_sync.st_ctime_ns != opened.st_ctime_ns
            or after_sync.st_mtime_ns != opened.st_mtime_ns
            or after_path.st_dev != opened.st_dev
            or after_path.st_ino != opened.st_ino
            or stat.S_IFMT(after_path.st_mode) != stat.S_IFMT(opened.st_mode)
            or after_path.st_ctime_ns != opened.st_ctime_ns
        ):
            raise RuntimeError(
                "Remote-write safety marker changed while it was synchronised"
            )
    finally:
        os.close(fd)

    data = b"".join(chunks)
    if len(data) != opened.st_size:
        raise RuntimeError("Remote-write safety marker read was incomplete")
    return (
        opened.st_dev,
        opened.st_ino,
        stat.S_IFMT(opened.st_mode),
        opened.st_ctime_ns,
        data,
    )


def read_remote_write_safety_barrier_snapshot(
    *,
    AMBIGUOUS_POST_OUTCOME_FILE: Any,
    AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE: Any,
    latch_remote_write_safety_marker_observation: Any,
    os: Any,
    read_remote_write_safety_marker_snapshot: Any,
) -> tuple[Path, tuple[int, int, int, int, bytes]]:
    """Return one exact supported marker/successor state.

    The only supported two-name state is the fixed original/successor pair
    referring to one inode with exactly two links.  A sole successor with one
    link is the expected restart state after loss of the original pathname.
    Any other hard link, replacement, type change or identity split fails
    closed.
    """

    entries: dict[Path, os.stat_result] = {}
    for path in (
        AMBIGUOUS_POST_OUTCOME_FILE,
        AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
    ):
        try:
            entries[path] = os.lstat(path)
        except FileNotFoundError:
            continue
        except Exception:
            latch_remote_write_safety_marker_observation()
            raise

    if not entries:
        raise FileNotFoundError("No remote-write safety barrier exists")
    latch_remote_write_safety_marker_observation()

    original = entries.get(AMBIGUOUS_POST_OUTCOME_FILE)
    successor = entries.get(AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE)

    if original is not None and successor is None:
        if not stat.S_ISREG(original.st_mode) or original.st_nlink != 1:
            raise RuntimeError(
                "A sole remote-write safety marker must be one ordinary "
                "single-link file"
            )
        # A running process must never migrate this legacy-only state.  The
        # pathname could disappear before the successor link is committed,
        # leaving no restart-persistent barrier after a hard process loss.
        # Only the stopped, lock-bound reconciler may first establish the
        # successor; activation is permitted only after all markers are gone.
        raise RuntimeError(
            "A legacy-only remote-write safety marker requires stopped "
            "offline reconciliation before protocol activation"
        )

    if original is None and successor is not None:
        if not stat.S_ISREG(successor.st_mode) or successor.st_nlink != 1:
            raise RuntimeError(
                "A sole remote-write safety successor must be one ordinary "
                "single-link file"
            )
        return (
            AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
            read_remote_write_safety_marker_snapshot(
                AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
            ),
        )

    assert original is not None and successor is not None
    if (
        not stat.S_ISREG(original.st_mode)
        or not stat.S_ISREG(successor.st_mode)
        or original.st_nlink != 2
        or successor.st_nlink != 2
        or original.st_dev != successor.st_dev
        or original.st_ino != successor.st_ino
    ):
        raise RuntimeError(
            "Remote-write safety marker and successor are not one exact "
            "two-link ordinary-file pair"
        )
    original_snapshot = read_remote_write_safety_marker_snapshot(
        AMBIGUOUS_POST_OUTCOME_FILE,
        accepted_link_counts=frozenset({2}),
    )
    successor_snapshot = read_remote_write_safety_marker_snapshot(
        AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
        accepted_link_counts=frozenset({2}),
    )
    if original_snapshot != successor_snapshot:
        raise RuntimeError(
            "Remote-write safety marker and successor snapshots differ"
        )
    return AMBIGUOUS_POST_OUTCOME_FILE, original_snapshot


def acknowledge_durable_remote_write_safety_marker(
    *,
    expected_bytes: bytes | None = None,
    AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE: Any,
    fsync_parent_dir: Any,
    read_remote_write_safety_barrier_snapshot: Any,
    require_remote_write_marker_removal_protocol: Any,
) -> bool:
    """Synchronise and revalidate one unchanged marker namespace entry."""
    require_remote_write_marker_removal_protocol()
    active_path, before = read_remote_write_safety_barrier_snapshot()
    if expected_bytes is not None and before[-1] != expected_bytes:
        raise RuntimeError(
            "Remote-write safety marker does not match the expected incident"
        )

    # File contents are synchronised by the snapshot helper.  The directory
    # fsync makes the name-to-inode binding durable; the second no-follow read
    # proves that the name still identifies the same ordinary file afterwards.
    fsync_parent_dir(active_path, strict=True)
    after_path, after = read_remote_write_safety_barrier_snapshot()
    # Removing the legacy hard-link name legitimately changes inode ctime.
    # The separately synchronised successor remains a complete restart
    # barrier when its device, inode, type and exact bytes are unchanged.
    unchanged_successor_survivor = (
        after_path == AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE
        and before[:3] == after[:3]
        and before[-1] == after[-1]
    )
    if before != after and not unchanged_successor_survivor:
        raise RuntimeError(
            "Remote-write safety marker disappeared, changed or was replaced "
            "during durability acknowledgement"
        )
    if expected_bytes is not None and after[-1] != expected_bytes:
        raise RuntimeError(
            "Remote-write safety marker changed from the expected incident"
        )
    # The supported offline reconciler must still be excluded by the exact
    # process-lifetime lock after the final marker identity/content check.
    require_remote_write_marker_removal_protocol()
    return True


def ensure_durable_remote_write_safety_marker(
    marker: dict,
    *,
    AMBIGUOUS_POST_OUTCOME_FILE: Any,
    AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE: Any,
    acknowledge_durable_remote_write_safety_marker: Any,
    atomic_write_json: Any,
    log: Any,
    os: Any,
    remote_write_safety_protocol_is_active: Any,
) -> bool:
    """Write or acknowledge a marker without trusting atomic-write return alone."""
    if not remote_write_safety_protocol_is_active():
        raise RuntimeError(
            "Cannot record a remote-write incident while the restart-persistent "
            "safety protocol is inactive"
        )
    expected_bytes = canonical_atomic_json_bytes(marker)
    original_exists = False
    successor_exists = False
    try:
        os.lstat(AMBIGUOUS_POST_OUTCOME_FILE)
    except FileNotFoundError:
        pass
    else:
        original_exists = True
    try:
        os.lstat(AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE)
    except FileNotFoundError:
        pass
    else:
        successor_exists = True
    if not original_exists and not successor_exists:
        try:
            atomic_write_json(
                AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
                marker,
                durable=True,
            )
        except Exception:
            # Replacement can succeed before the final directory fsync raises.
            # The central acknowledgement below decides whether exact durable
            # bytes now exist; the writer's return status is not authoritative.
            pass
        # Prove the successor's exact bytes and parent-directory durability
        # before exposing the optional legacy name to any later fallible step.
        # A hard exit from this point onward therefore leaves a complete
        # restart barrier even if the legacy hard-link is never created.
        acknowledge_durable_remote_write_safety_marker(
            expected_bytes=expected_bytes,
        )
        try:
            os.link(
                AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
                AMBIGUOUS_POST_OUTCOME_FILE,
                follow_symlinks=False,
            )
        except FileExistsError:
            # The exact namespace is revalidated below.  Never replace an
            # entry which appeared after the initial absence check.
            pass
        except Exception:
            # The successor is the restart barrier.  A legacy display name is
            # useful for compatibility, but inability to add it must not
            # discard an otherwise exact durable successor.
            log.warning(
                "Could not add the legacy remote-write safety marker name; "
                "retaining the successor-only barrier",
                exc_info=True,
            )
    return acknowledge_durable_remote_write_safety_marker(
        expected_bytes=expected_bytes,
    )


def durable_remote_write_safety_barrier_exists(
    *,
    MEDIA_UPLOAD_RECEIPT_FILE: Any,
    durable_remote_write_safety_marker_exists: Any,
    historical_context_reply_store: Any,
    load_confirmed_reply_receipt: Any,
    load_meme_post_receipt: Any,
    load_regular_post_receipt: Any,
    media_upload_has_valid_restart_barrier: Any,
    release_retained_sigint_deferral_after_durable_barrier: Any,
    remote_write_safety_protocol_is_active: Any,
    remote_write_transport_journal_paths: Any,
    transport_journal_has_valid_restart_barrier: Any,
) -> bool:
    """Return whether restart safety survives loss of the process latch."""
    def confirmed() -> bool:
        # Any independently validated incident-specific durable authority—not
        # only the legacy marker—makes controlled process loss restart-safe.
        # Release a retained post-confirmation SIGINT exactly at that point.
        release_retained_sigint_deferral_after_durable_barrier()
        return True

    # Protocol inactivity blocks every compatible process, but it is not
    # incident-specific durable evidence and must not by itself acknowledge an
    # in-flight transaction or release a retained confirmed-post signal guard.
    # Supported clean activation can occur only after every receipt and marker
    # has been reconciled.
    if (
        remote_write_safety_protocol_is_active()
        and durable_remote_write_safety_marker_exists()
    ):
        return True
    # A conservative blocker and proved restart-persistent authority are
    # different claims.  Directory inspection errors, unsafe namespace entries
    # and malformed objects must keep remote writes blocked, but cannot release
    # a retained SIGINT.  Require at least one strict transaction object.
    for journal_path in remote_write_transport_journal_paths():
        try:
            if transport_journal_has_valid_restart_barrier(journal_path):
                return confirmed()
        except Exception:
            continue
    try:
        if media_upload_has_valid_restart_barrier(MEDIA_UPLOAD_RECEIPT_FILE):
            return confirmed()
    except Exception:
        pass
    # Retirement auxiliaries are blockers, but an invalid or uninspectable
    # auxiliary alone is not sufficient durability evidence.  The normal
    # per-tick resumer will either complete a valid retirement or leave the
    # signal guard retained.
    try:
        from historical_context_formatter import HistoricalContextReplyStore

        historical_loaded = (
            historical_context_reply_store()._load_receipt_safely()
        )
        if historical_loaded is not None and (
            HistoricalContextReplyStore._valid_sending_receipt(
                historical_loaded[0]
            )
            or HistoricalContextReplyStore._valid_receipt(historical_loaded[0])
        ):
            return confirmed()
    except Exception:
        pass
    try:
        regular_status, _regular = load_regular_post_receipt()
        meme_status, _meme = load_meme_post_receipt()
        if regular_status in {"sending", "pending_schedule", "valid"}:
            return confirmed()
        if meme_status in {"sending", "pending_schedule", "valid"}:
            return confirmed()
    except Exception:
        pass
    try:
        status, _receipt = load_confirmed_reply_receipt()
    except Exception:
        return False
    return confirmed() if status in {"sending", "legacy_sending", "valid"} else False

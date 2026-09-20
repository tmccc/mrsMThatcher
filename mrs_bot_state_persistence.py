"""Publish validated, sealed state generations and exact commit proofs.

Every document is encoded and reader-validated before any backup mutation.
Canonical replacement and directory fsync establish authority independently of
replica publication. Current paths, reader policy and lock ownership are supplied
by the root. StateBackups owns exact copying and backup-generation rotation;
canonical publication retains its existing runtime callbacks and commit proofs.
This module performs no import-time runtime work."""

from __future__ import annotations

import copy
import hashlib

from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from mrs_bot_observability import state_debug_summary

if TYPE_CHECKING:
    from mrs_bot_state_generation import StateCommitProof


def state_document_for_persistence(
    state: dict,
    *,
    STATE_FILE: Path,
    STATE_MINIMUM_READER_VERSION: int,
    STATE_PREVIOUS_READER_COMPATIBILITY_FENCES: tuple[dict, ...],
    STATE_READER_COMPATIBILITY_FENCE: dict,
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


@dataclass(frozen=True)
class StateBackups:
    """Own exact backup copies and rotation without publishing canonical state."""

    unsafe_namespace: type[Exception]
    fsync_parent: Callable[..., None]
    os: ModuleType
    read_stable_bytes: Callable[..., tuple[bool, bytes | None]]
    tempfile: ModuleType
    backup_count: int
    state_file: Path
    log: Logger

    def copy(self, src: Path, dst: Path, *, durable: bool=False) -> None:
        """Copy one exact stable state generation without following links."""

        present, data = self.read_stable_bytes(src)
        if not present or data is None:
            raise self.unsafe_namespace(
                f"state backup source disappeared before copying: {src}"
            )
        dst.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = self.tempfile.mkstemp(
            prefix=f".{dst.name}.",
            dir=dst.parent,
        )
        temporary = Path(temporary_name)
        try:
            with self.os.fdopen(descriptor, "wb") as handle:
                self.os.fchmod(handle.fileno(), 0o600)
                handle.write(data)
                if durable:
                    handle.flush()
                    self.os.fsync(handle.fileno())
            self.os.replace(temporary, dst)
            if durable:
                self.fsync_parent(dst, strict=True)
        except BaseException:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise

    def rotate(self, *, durable: bool=False) -> None:
        """Rotate state backups before commit."""
        if self.backup_count <= 1 or not self.state_file.exists():
            return

        try:
            for i in range(self.backup_count, 2, -1):
                older = self.state_file.with_name(f"{self.state_file.name}.bak{i - 1}")
                newer = self.state_file.with_name(f"{self.state_file.name}.bak{i}")
                if older.exists():
                    older.replace(newer)

            bak2 = self.state_file.with_name(f"{self.state_file.name}.bak2")
            self.copy(self.state_file, bak2, durable=durable)
            self.log.debug("Previous state backup written: %s", bak2)
        except Exception:
            self.log.exception("Failed rotating state backups; continuing with state save")

    def write_latest(self, *, durable: bool=False) -> None:
        """Write latest state backup."""
        if self.backup_count <= 0 or not self.state_file.exists():
            return
        bak1 = self.state_file.with_name(f"{self.state_file.name}.bak1")
        self.copy(self.state_file, bak1, durable=durable)
        self.log.debug("Latest committed state backup written: %s", bak1)


def save_state(
    state: dict,
    *,
    durable: bool = False,
    STATE_FILE: Path,
    StateBackupWriteError: type[Exception],
    fsync_parent_dir: Callable[..., None],
    log: Logger,
    log_json_debug: Callable[..., None],
    os: ModuleType,
    rotate_state_backups_before_commit: Callable[..., None],
    state_document_for_persistence: Callable[..., dict],
    tempfile: ModuleType,
    test_process_production_state_write_blocked: Callable[..., bool],
    write_latest_state_backup: Callable[..., None],
    state_generation_context: Callable,
) -> StateCommitProof:
    """Persist state atomically, logging only a value-free structural summary."""
    if test_process_production_state_write_blocked(STATE_FILE):
        raise RuntimeError(f"Refusing test-process write to production state: {STATE_FILE}")
    log.debug("Saving state to %s", STATE_FILE)
    log_json_debug("State summary being saved", state_debug_summary(state))
    from mrs_bot_state_generation import (
        GENERATION_KEY, StateCommitProof, canonical_bytes, directory_identity,
        encode_generation, strict_document,
    )

    persisted_state = state_document_for_persistence(state)
    context = state_generation_context()
    # Serialize a detached document before touching even a backup. This also
    # rejects unsupported Python objects and nonfinite values in extension data.
    candidate = strict_document(canonical_bytes(persisted_state))
    if context.validate(candidate, path=STATE_FILE) is None:
        raise ValueError('state document fails persisted-state validation')
    # Check the size of the complete envelope before any directory/file mutation.
    context.require_lock('state generation publication')
    sequence = context.next_sequence(STATE_FILE)
    persisted_state, data = encode_generation(candidate, sequence, context.maximum_bytes)
    if context.validate(strict_document(data), path=STATE_FILE) is None:
        raise ValueError('state document fails persisted-state validation')

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = directory_identity(STATE_FILE.parent)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{STATE_FILE.name}.",
        dir=STATE_FILE.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(data)
            # Every published generation is a commit, including periodic saves.
            handle.flush()
            os.fsync(handle.fileno())
            committed_identity = os.fstat(handle.fileno())

        rotate_state_backups_before_commit(durable=durable)
        context.require_lock('state generation replacement')
        if directory_identity(STATE_FILE.parent) != directory:
            raise RuntimeError('state directory changed before replacement')
        os.replace(temporary, STATE_FILE)
        fsync_parent_dir(STATE_FILE, strict=True)
        current_identity = os.lstat(STATE_FILE)
        if (current_identity.st_dev, current_identity.st_ino) != (
            committed_identity.st_dev, committed_identity.st_ino
        ):
            raise RuntimeError('state inode changed during commit')
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    proof = StateCommitProof(
        STATE_FILE, directory, tuple(getattr(current_identity, field) for field in (
            "st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_size",
            "st_ctime_ns", "st_mtime_ns")),
        hashlib.sha256(data).hexdigest(), sequence, context.maximum_bytes,
        receipt_digests=frozenset(persisted_state.get('_confirmed_receipt_commits', {})),
    )
    proof.require_current()
    state[GENERATION_KEY] = persisted_state[GENERATION_KEY]
    try:
        write_latest_state_backup(durable=durable)
    except Exception as exc:
        error = StateBackupWriteError(
            f"Canonical state committed but latest backup write failed: {STATE_FILE}"
        )
        error.commit_proof = proof
        raise error from exc
    proof.require_current()
    return proof

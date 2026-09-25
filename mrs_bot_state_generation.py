"""Versioned state generations and exact, restart-safe commit authority.

The sequence and digest live in the atomically replaced document itself. A
backup is a replica, never a second commit record. Equal sequences with unequal
digests are ambiguous; legacy documents have no ordering authority. Callers
hold the instance/state lock across selection, migration and publication.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from mrs_bot_core_contracts import BotState


GENERATION_KEY = '_state_generation'
MAX_SEQUENCE = 2**63 - 1


def canonical_bytes(value: object) -> bytes:
    """Encode once for validation, digest calculation and publication."""
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False).encode('utf-8')


def strict_document(data: bytes) -> dict:
    """Reject duplicate object names, nonfinite constants and nonobjects."""
    def object_pairs(pairs: list[tuple[str, object]]) -> dict:
        """Build an object only if each name occurs once."""
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate state JSON name')
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        """Reject nonstandard JSON numbers before schema validation."""
        raise ValueError('nonfinite state JSON number')

    value = json.loads(data.decode('utf-8'), object_pairs_hook=object_pairs,
                       parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError('state document must be an object')
    return value


def generation_number(document: dict) -> int:
    """Verify an embedded generation, returning zero for an unfenced legacy file."""
    generation = document.get(GENERATION_KEY)
    if generation is None and GENERATION_KEY not in document:
        if document.get('minimum_reader_version', 1) >= 5:
            raise ValueError('generation-aware state has no generation')
        return 0
    if (not isinstance(generation, dict)
            or set(generation) != {'sequence', 'sha256'}
            or type(generation.get('sequence')) is not int
            or not 1 <= generation['sequence'] <= MAX_SEQUENCE):
        raise ValueError('invalid state generation')
    unsigned = dict(document)
    unsigned[GENERATION_KEY] = {'sequence': generation['sequence']}
    if hashlib.sha256(canonical_bytes(unsigned)).hexdigest() != generation.get('sha256'):
        raise ValueError('state generation digest mismatch')
    return generation['sequence']


def encode_generation(document: dict, sequence: int, maximum_bytes: int) -> tuple[dict, bytes]:
    """Seal the complete document and reject over-limit growth before any I/O."""
    if not 1 <= sequence <= MAX_SEQUENCE:
        raise ValueError('state generation sequence exhausted')
    document = dict(document)
    document[GENERATION_KEY] = {'sequence': sequence}
    digest = hashlib.sha256(canonical_bytes(document)).hexdigest()
    document[GENERATION_KEY]['sha256'] = digest
    data = canonical_bytes(document)
    if len(data) > maximum_bytes:
        raise ValueError('state document exceeds reader byte limit')
    return document, data


def directory_identity(path: Path) -> tuple[int, int, int, int]:
    """Require an owned directory whose namespace is not writable by others."""
    from exact_receipt_retirement import ExactReceiptRetirementError, _open_directory

    try:
        descriptor = _open_directory(path)
    except ExactReceiptRetirementError as exc:
        raise ValueError('unsafe durable state directory ownership or permissions') from exc
    try:
        metadata = os.fstat(descriptor)
        current = os.lstat(path)
        if (metadata.st_dev, metadata.st_ino, metadata.st_uid, metadata.st_mode) != (
                current.st_dev, current.st_ino, current.st_uid, current.st_mode):
            raise ValueError('durable state directory changed while opening')
    finally:
        os.close(descriptor)
    return metadata.st_dev, metadata.st_ino, metadata.st_uid, metadata.st_mode


def file_identity(path: Path) -> tuple[int, ...]:
    """Record every identity and stability field used by the secure reader."""
    metadata = os.lstat(path)
    return tuple(getattr(metadata, field) for field in (
        'st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_uid', 'st_size',
        'st_ctime_ns', 'st_mtime_ns'))


def receipt_commit_records_are_valid(commits: object) -> bool:
    """Validate the exact durable receipt-commit record shape without copying it."""
    return not (
        not isinstance(commits, dict)
        or any(
            not isinstance(key, str) or len(key) != 64
            or any(character not in '0123456789abcdef' for character in key)
            or not isinstance(value, dict)
            or set(value) != {'quote_hash', 'image_basename'}
            or any(not isinstance(item, str) for item in value.values())
            for key, value in commits.items()
        )
    )


def record_receipt_commit(state: BotState, receipt: Mapping[str, Any]) -> None:
    """Bind exact receipt retirement to the state which records its confirmed effect.

    This durable identity collection is intentionally never truncated. The whole
    encoded-state ceiling rejects growth before any prior authority is changed.
    It also lets a fresh process reprove a retirement whose source has moved.
    """
    digest = hashlib.sha256(canonical_bytes(receipt) + b'\n').hexdigest()
    entries = state.setdefault('_confirmed_receipt_commits', {})
    identity = receipt.get('selected_identity', receipt)
    entries[digest] = {
        'quote_hash': str(identity.get('quote_hash') or ''),
        'image_basename': str(identity.get('image_basename') or ''),
    }


@dataclass(frozen=True)
class StateCommitProof:
    """Exact canonical generation, after file and parent directory fsync."""

    path: Path
    directory: tuple[int, int, int, int]
    identity: tuple[int, ...]
    sha256: str
    sequence: int
    maximum_bytes: int
    protected_files: tuple[StateCommitProof, ...] = ()
    receipt_digests: frozenset[str] = frozenset()

    def require_receipt(self, receipt: Mapping[str, object]) -> None:
        """Require this exact source receipt's confirmed effect in the sealed state."""
        digest = hashlib.sha256(canonical_bytes(receipt) + b'\n').hexdigest()
        if digest not in self.receipt_digests:
            raise RuntimeError('state commit does not bind this exact receipt')
        self.require_current()

    def require_current(self) -> None:
        """Reprove the exact committed inode and bytes before evidence retirement."""
        from mrs_bot_durable_json_io import (
            durable_state_namespace_is_owned_single_link_file,
            read_stable_owned_json_bytes_no_follow,
        )

        def owned(path: Path) -> bool:
            """Apply the same size and authority policy as state loading."""
            return durable_state_namespace_is_owned_single_link_file(
                path, maximum_bytes=self.maximum_bytes, os=os)

        if directory_identity(self.path.parent) != self.directory:
            raise RuntimeError('state commit directory identity changed')
        if file_identity(self.path) != self.identity:
            raise RuntimeError('state commit file identity changed')
        present, data = read_stable_owned_json_bytes_no_follow(
            self.path, DURABLE_RUNTIME_JSON_MAX_BYTES=self.maximum_bytes,
            UnsafeDurableStateNamespace=RuntimeError,
            durable_state_namespace_is_owned_single_link_file=owned, os=os)
        if (not present or data is None
                or hashlib.sha256(data).hexdigest() != self.sha256
                or file_identity(self.path) != self.identity
                or directory_identity(self.path.parent) != self.directory):
            raise RuntimeError('state commit changed before evidence retirement')
        for protected in self.protected_files:
            protected.require_current()


def protect_history_files(proof: StateCommitProof, files: tuple[tuple[Path, bytes], ...]) -> StateCommitProof:
    """Bind independently fsynced, exact used histories to the state commit proof."""
    protected = []
    for path, expected in files:
        directory = directory_identity(path.parent)
        before = file_identity(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != before[:2]:
                raise RuntimeError('used history changed before durable proof')
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        item = StateCommitProof(path, directory, before,
                                hashlib.sha256(expected).hexdigest(), 0, proof.maximum_bytes)
        item.require_current()
        protected.append(item)
    result = replace(proof, protected_files=tuple(protected))
    result.require_current()
    return result


def require_unambiguous_legacy_documents(documents: Mapping[Path, Mapping[str, object]], primary_path: Path) -> None:
    """Require legacy agreement unless the canonical/latest pair proves a commit.

    Backup numbers alone cannot order legacy data: a failed latest-backup
    publication followed by rotation can leave bak2 newer than bak1. An equal
    primary/bak1 pair proves the old protocol completed publication; otherwise
    every usable legacy document must agree before migration can discard it.
    """
    encoded = {path: canonical_bytes(document) for path, document in documents.items()}
    latest_path = primary_path.with_name(f'{primary_path.name}.bak1')
    if primary_path in encoded and latest_path in encoded:
        if encoded[primary_path] == encoded[latest_path]:
            return
        raise RuntimeError(
            'Primary state and latest committed backup are both valid but diverge; '
            'refusing to guess which durable generation is newer'
        )
    if len(set(encoded.values())) > 1:
        raise RuntimeError('ambiguous legacy state generations; refusing to guess during migration')


@dataclass(frozen=True)
class StateGenerationContext:
    """Explicit current reader and lock dependencies for one state publication."""

    maximum_bytes: int
    backup_count: int
    validate: Callable[..., dict | None]
    read: Callable[..., tuple[bool, bytes | None]]
    require_lock: Callable[[str], None]

    def next_sequence(self, path: Path) -> int:
        """Refuse ambiguous legacy/colliding documents before choosing a successor."""
        generations: dict[int, bytes] = {}
        legacy: dict[Path, dict] = {}
        for index in range(self.backup_count + 1):
            candidate = path if index == 0 else path.with_name(f'{path.name}.bak{index}')
            present, data = self.read(candidate)
            if not present or data is None:
                continue
            try:
                document = strict_document(data)
            except ValueError:
                # Incomplete generations are not publication authority. The
                # loader has already established a usable state under the lock.
                continue
            normalized = self.validate(strict_document(data), path=candidate)
            if normalized is None:
                # The reader can recover discardable pending-candidate
                # metadata. Its sealed sequence must still precede the repair
                # commit, and divergent legacy recovery documents still veto
                # guessing, exactly as in load_state's final recovery pass.
                normalized = self.validate(
                    strict_document(data), path=candidate,
                    recover_pending_identity=True,
                )
                if normalized is None:
                    continue
            try:
                sequence = generation_number(document)
            except ValueError:
                continue
            if sequence:
                encoded = canonical_bytes(document)
                if sequence in generations and generations[sequence] != encoded:
                    raise RuntimeError('conflicting state generation identities')
                generations[sequence] = encoded
            else:
                normalized.pop('pending_reply_drafts', None)
                legacy[candidate] = normalized
        if not generations:
            require_unambiguous_legacy_documents(legacy, path)
        return max(generations, default=0) + 1


def require_commit_proof(proof: StateCommitProof | None) -> None:
    """Refuse to retire suppression evidence without an exact durable commit."""
    if not isinstance(proof, StateCommitProof):
        raise RuntimeError('evidence retirement requires an exact durable state commit')
    proof.require_current()

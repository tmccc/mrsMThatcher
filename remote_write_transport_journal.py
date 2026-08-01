#!/usr/bin/env python3
"""Durable, single-transaction authority for public X post creation.

The lane-specific sending receipt is authoritative while a post is being
prepared.  Immediately before transport this module publishes a second,
payload-bound journal.  That journal remains a restart-visible global barrier
even if the lane receipt is removed after validation.  Process memory is used
only to prevent reuse within one interpreter; it is never the restart barrier.

The journal lifecycle is::

    prepared -> attempting -> confirmed -> retired

An ``attempting`` journal has an intentionally unknown remote outcome after an
abrupt process loss.  A ``confirmed`` journal binds the returned post ID while
the caller completes its lane-specific durable state.  Retirement is allowed
only while the exact confirmed lane receipt is still present, so at least one
restart-visible barrier remains throughout the supported transition.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import secrets
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from transaction_mutation_authority import (
    TransactionMutationAuthority,
    require_transaction_mutation_authority,
)


JOURNAL_BASENAME = "remote_write_transport_journal.json"
FENCE_BASENAME = "remote_write_transport_fence.json"
JOURNAL_SCHEMA_VERSION = 2
JOURNAL_MODE = 0o600
JOURNAL_MAX_BYTES = 128 * 1024
JOURNAL_STAGING_PREFIX = f".{JOURNAL_BASENAME}.transition."
JOURNAL_RETIREMENT_PREFIX = f".{JOURNAL_BASENAME}.retirement-guard."
LANE_SOURCE_VALIDATOR_ID = "mrs-lane-source-binding-v2"
_RENAME_EXCHANGE = 2
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_LANE_RE = re.compile(r"[a-z][a-z0-9_.:-]{0,79}")
_POST_ID_RE = re.compile(r"\d{1,30}")
_MEDIA_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_VALIDATOR_ID_RE = re.compile(r"[a-z][a-z0-9_.:-]{2,159}")
_consumed_authorities: set[tuple[str, str]] = set()
_issued_untransmitted_authorities: set[tuple[str, str, str]] = set()
_issued_source_bindings: set[
    tuple[str, str, int, int, int, str, str, str]
] = set()
_transitioning_transactions: set[tuple[str, str]] = set()
_aborting_transactions: set[tuple[str, str]] = set()
_authority_lock = threading.Lock()


class TransportJournalError(RuntimeError):
    """A transport journal is missing, unsafe, conflicting, or stale."""


class BoundSourceReceiptTransitionError(TransportJournalError):
    """An identity-bound source promotion did not complete exactly."""


@dataclass(frozen=True)
class TransportAuthority:
    """One in-process handle bound to an exact durable journal generation."""

    transaction_id: str
    journal_path: str
    journal_sha256: str
    journal_device: int
    journal_inode: int
    journal_ctime_ns: int
    fence_path: str
    fence_sha256: str
    fence_device: int
    fence_inode: int
    fence_ctime_ns: int
    payload_sha256: str
    lane: str
    source_receipt_basename: str
    source_validator_id: str
    lifecycle_state: str


@dataclass(frozen=True)
class FrozenTweetRequest:
    """Canonical, immutable representation of the supported X create request."""

    method: str
    request_path: str
    payload_bytes: bytes
    payload_sha256: str

    def payload(self) -> dict[str, Any]:
        """Return a fresh mutable copy of the frozen payload."""

        return _parse_strict_object_bytes(
            self.payload_bytes,
            label="frozen tweet payload",
        )


@dataclass(frozen=True)
class SourceReceiptBinding:
    """Exact source receipt and payload approved by a lane-owned validator."""

    receipt_path: str
    receipt_bytes: bytes
    receipt_sha256: str
    receipt_device: int
    receipt_inode: int
    receipt_ctime_ns: int
    receipt_size: int
    receipt_document: dict[str, Any]
    lane: str
    request: FrozenTweetRequest
    validator_id: str


@dataclass(frozen=True)
class ConfirmedTransportDetails:
    """Strict restart-recovery view of one confirmed transport transaction."""

    transaction_id: str
    lane: str
    post_id: str
    confirmation_epoch: int
    source_receipt_basename: str
    source_receipt_sha256: str
    source_validator_id: str
    payload_bytes: bytes
    payload_sha256: str
    journal_path: str
    journal_sha256: str
    journal_device: int
    journal_inode: int
    journal_ctime_ns: int

    def payload(self) -> dict[str, Any]:
        """Return a fresh copy of the exact confirmed remote payload."""

        return _parse_strict_object_bytes(
            self.payload_bytes,
            label="confirmed tweet payload",
        )


@dataclass(frozen=True)
class ConfirmedSourceRecovery:
    """Confirmed transport plus its still-exact, semantically valid source."""

    details: ConfirmedTransportDetails
    source_binding: SourceReceiptBinding


@dataclass(frozen=True)
class TransportJournalState:
    """Deterministic directory-level inspection of all journal barriers."""

    classification: str
    blocking: bool
    journal: JournalSnapshot | None
    fence: JournalSnapshot | None
    staging_names: tuple[str, ...]
    retirement_guard_names: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class JournalSnapshot:
    """One stable, canonical journal observation."""

    document: dict[str, Any]
    data: bytes
    device: int
    inode: int
    ctime_ns: int
    sha256: str


@dataclass(frozen=True)
class _StableFile:
    data: bytes
    metadata: os.stat_result


def canonical_json_bytes(value: object) -> bytes:
    """Return the only journal JSON representation."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _strict_json_copy(value: object, *, depth: int = 0) -> object:
    """Copy the supported JSON subset without Python-to-JSON coercions."""

    if depth > 24:
        raise TransportJournalError("tweet payload nesting is excessive")
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is list:
        return [_strict_json_copy(item, depth=depth + 1) for item in value]
    if type(value) is dict:
        copied: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str or not key:
                raise TransportJournalError(
                    "tweet payload keys must be non-empty strings"
                )
            copied[key] = _strict_json_copy(item, depth=depth + 1)
        return copied
    raise TransportJournalError(
        f"tweet payload contains unsupported value type: {type(value).__name__}"
    )


def freeze_tweet_request(
    *,
    method: str,
    request_path: str,
    payload: Mapping[str, Any],
) -> FrozenTweetRequest:
    """Validate and freeze the only public-create request shape used here."""

    if str(method).upper() != "POST" or request_path != "/2/tweets":
        raise TransportJournalError("transport request endpoint is invalid")
    if type(payload) is not dict:
        raise TransportJournalError("tweet payload must be an ordinary JSON object")
    copied = _strict_json_copy(payload)
    assert isinstance(copied, dict)
    allowed = {"text", "media", "reply", "made_with_ai"}
    if not set(copied).issubset(allowed) or not ({"text", "media"} & set(copied)):
        raise TransportJournalError("tweet payload fields are invalid")
    if "text" in copied and (
        type(copied["text"]) is not str or not copied["text"]
    ):
        raise TransportJournalError("tweet text is invalid")
    if "media" in copied:
        media = copied["media"]
        if (
            type(media) is not dict
            or set(media) != {"media_ids"}
            or type(media.get("media_ids")) is not list
            or not 1 <= len(media["media_ids"]) <= 4
            or any(
                    type(item) is not str or not _MEDIA_ID_RE.fullmatch(item)
                for item in media["media_ids"]
            )
        ):
            raise TransportJournalError("tweet media binding is invalid")
    if "reply" in copied:
        reply = copied["reply"]
        if (
            type(reply) is not dict
            or set(reply) != {"in_reply_to_tweet_id"}
            or type(reply.get("in_reply_to_tweet_id")) is not str
            or not _POST_ID_RE.fullmatch(reply["in_reply_to_tweet_id"])
        ):
            raise TransportJournalError("tweet reply binding is invalid")
    if "made_with_ai" in copied and copied["made_with_ai"] is not True:
        raise TransportJournalError("made_with_ai must be omitted or true")
    data = canonical_json_bytes(copied)
    if len(data) > JOURNAL_MAX_BYTES:
        raise TransportJournalError("tweet payload exceeds its size limit")
    return FrozenTweetRequest(
        method="POST",
        request_path="/2/tweets",
        payload_bytes=data,
        payload_sha256=hashlib.sha256(data).hexdigest(),
    )


def payload_sha256(payload: Mapping[str, Any]) -> str:
    """Hash an exact remote payload using canonical JSON."""

    return freeze_tweet_request(
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    ).payload_sha256


def bind_transport_source(
    *,
    receipt_path: Path,
    expected_receipt: Mapping[str, Any],
    expected_receipt_bytes: bytes | None = None,
    lane: str,
    payload: Mapping[str, Any],
    validator_id: str,
    validator: Callable[[str, Mapping[str, Any], Mapping[str, Any]], bool],
) -> SourceReceiptBinding:
    """Bind exact bytes only after a lane-owned semantic validator approves.

    The validator receives independent copies of the lane, parsed receipt and
    frozen payload.  It must return the singleton ``True``.  Merely passing an
    expected dictionary is not semantic authority.
    """

    if type(lane) is not str or not _LANE_RE.fullmatch(lane):
        raise TransportJournalError("transport lane is invalid")
    if type(validator_id) is not str or not _VALIDATOR_ID_RE.fullmatch(
        validator_id
    ):
        raise TransportJournalError("source validator ID is invalid")
    if not callable(validator):
        raise TransportJournalError("source validator is not callable")
    receipt_path = Path(receipt_path)
    try:
        receipt = _read_stable_regular(
            receipt_path,
            maximum=JOURNAL_MAX_BYTES,
            expected_mode=JOURNAL_MODE,
        )
    except FileNotFoundError as exc:
        raise TransportJournalError("source receipt is not durably present") from exc
    parsed = _parse_strict_object_bytes(receipt.data, label="source receipt")
    canonical_expected = (
        canonical_json_bytes(dict(expected_receipt))
        if expected_receipt_bytes is None
        else expected_receipt_bytes
    )
    if (
        type(canonical_expected) is not bytes
        or receipt.data != canonical_expected
        or parsed != dict(expected_receipt)
    ):
        raise TransportJournalError("source receipt does not match prepared transaction")
    request = freeze_tweet_request(
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    )
    try:
        approved = validator(str(lane), dict(parsed), request.payload())
    except Exception as exc:
        raise TransportJournalError("source receipt semantic validation failed") from exc
    if approved is not True:
        raise TransportJournalError("source receipt does not semantically bind payload")
    binding = SourceReceiptBinding(
        receipt_path=str(receipt_path.absolute()),
        receipt_bytes=receipt.data,
        receipt_sha256=hashlib.sha256(receipt.data).hexdigest(),
        receipt_device=int(receipt.metadata.st_dev),
        receipt_inode=int(receipt.metadata.st_ino),
        receipt_ctime_ns=int(receipt.metadata.st_ctime_ns),
        receipt_size=len(receipt.data),
        receipt_document=parsed,
        lane=str(lane),
        request=request,
        validator_id=str(validator_id),
    )
    with _authority_lock:
        _issued_source_bindings.add(_source_binding_key(binding))
    return binding


def _source_binding_key(
    binding: SourceReceiptBinding,
) -> tuple[str, str, int, int, int, str, str, str]:
    return (
        binding.receipt_path,
        binding.receipt_sha256,
        binding.receipt_device,
        binding.receipt_inode,
        binding.receipt_ctime_ns,
        binding.request.payload_sha256,
        binding.lane,
        binding.validator_id,
    )


def journal_path_for_receipt(receipt_path: Path) -> Path:
    """Return the one sibling journal shared by all public-create lanes."""

    return Path(receipt_path).parent / JOURNAL_BASENAME


def fence_path_for_journal(journal_path: Path) -> Path:
    """Return the immutable companion barrier for one transport journal."""

    return Path(journal_path).parent / FENCE_BASENAME


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TransportJournalError(
                f"transport journal contains duplicate JSON key: {key}"
            )
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise TransportJournalError(
        f"transport journal contains invalid JSON constant: {value}"
    )


def _parse_strict_object_bytes(data: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except TransportJournalError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransportJournalError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise TransportJournalError(f"{label} must be a JSON object")
    return value


def _parse_canonical(data: bytes, *, label: str) -> dict[str, Any]:
    value = _parse_strict_object_bytes(data, label=label)
    if canonical_json_bytes(value) != data:
        raise TransportJournalError(f"{label} is not canonical JSON")
    return value


def _metadata_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_nlink),
        int(value.st_uid),
        int(value.st_size),
        int(value.st_ctime_ns),
        int(value.st_mtime_ns),
    )


def _read_all(descriptor: int, maximum: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(8192, maximum + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum:
            raise TransportJournalError("durable transaction file is oversized")


def _read_stable_regular(
    path: Path,
    *,
    maximum: int,
    expected_mode: int | None = None,
    allowed_link_counts: frozenset[int] = frozenset({1}),
) -> _StableFile:
    """Read one owned, single-link ordinary file without following links."""

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise TransportJournalError("O_NOFOLLOW is required")
    try:
        before = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise TransportJournalError(f"cannot inspect durable file: {path.name}") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink not in allowed_link_counts
        or before.st_uid != os.geteuid()
        or before.st_size <= 0
        or before.st_size > maximum
        or (
            expected_mode is not None
            and stat.S_IMODE(before.st_mode) != expected_mode
        )
    ):
        raise TransportJournalError(f"durable file has unsafe metadata: {path.name}")
    descriptor = os.open(
        path,
        os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        opened = os.fstat(descriptor)
        data = _read_all(descriptor, maximum)
        after_fd = os.fstat(descriptor)
        after_path = os.stat(path, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise TransportJournalError(
            f"durable file changed while inspected: {path.name}"
        ) from exc
    finally:
        os.close(descriptor)
    identity = _metadata_identity(opened)
    if (
        _metadata_identity(before) != identity
        or _metadata_identity(after_fd) != identity
        or _metadata_identity(after_path) != identity
        or len(data) != opened.st_size
    ):
        raise TransportJournalError(
            f"durable file changed while inspected: {path.name}"
        )
    return _StableFile(data=data, metadata=opened)


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    written = 0
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count <= 0:
            raise OSError("short write while publishing transport journal")
        written += count


def _open_directory(path: Path) -> int:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise TransportJournalError("transport journal directory is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise TransportJournalError("transport journal parent is not a directory")
    return os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))


def _publish_new(path: Path, data: bytes) -> None:
    """Publish a new journal without an overwrite window.

    An interrupted write deliberately leaves a visible invalid final pathname,
    which all callers treat as a global barrier.
    """

    if len(data) > JOURNAL_MAX_BYTES:
        raise TransportJournalError("transport journal exceeds its size limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | nofollow
        | getattr(os, "O_CLOEXEC", 0),
        JOURNAL_MODE,
    )
    try:
        _write_all(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_fd = _open_directory(path.parent)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _rename_exchange(directory_fd: int, first: str, second: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise TransportJournalError("renameat2(RENAME_EXCHANGE) is required")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if (
        renameat2(
            directory_fd,
            os.fsencode(first),
            directory_fd,
            os.fsencode(second),
            _RENAME_EXCHANGE,
        )
        != 0
    ):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), first, second)


def _replace_exact(
    path: Path,
    *,
    expected: bytes,
    replacement: bytes,
    expected_device: int,
    expected_inode: int,
    expected_ctime_ns: int,
) -> None:
    """Atomically exchange a journal generation and prove the displaced bytes."""

    token = secrets.token_hex(16)
    staging_name = f"{JOURNAL_STAGING_PREFIX}{token}"
    staging = path.parent / staging_name
    descriptor = os.open(
        staging,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        JOURNAL_MODE,
    )
    try:
        _write_all(descriptor, replacement)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_fd = _open_directory(path.parent)
    exchanged = False
    try:
        os.fsync(directory_fd)
        before_exchange = _read_stable_regular(
            path,
            maximum=JOURNAL_MAX_BYTES,
            expected_mode=JOURNAL_MODE,
        )
        if (
            before_exchange.data != expected
            or (
                int(before_exchange.metadata.st_dev),
                int(before_exchange.metadata.st_ino),
                int(before_exchange.metadata.st_ctime_ns),
            )
            != (expected_device, expected_inode, expected_ctime_ns)
        ):
            raise TransportJournalError(
                "transport journal changed before atomic lifecycle transition"
            )
        _rename_exchange(directory_fd, path.name, staging_name)
        exchanged = True
        os.fsync(directory_fd)
        displaced = _read_stable_regular(
            staging,
            maximum=JOURNAL_MAX_BYTES,
            expected_mode=JOURNAL_MODE,
        )
        current = _read_stable_regular(
            path,
            maximum=JOURNAL_MAX_BYTES,
            expected_mode=JOURNAL_MODE,
        )
        # RENAME_EXCHANGE itself advances the displaced inode's ctime.  The
        # stable pre-exchange read proves the authorised ctime; device/inode
        # after exchange proves that no different pathname generation won the
        # remaining interval.
        displaced_identity_matches = (
            int(displaced.metadata.st_dev),
            int(displaced.metadata.st_ino),
        ) == (
            expected_device,
            expected_inode,
        )
        if (
            not displaced_identity_matches
            or displaced.data != expected
            or current.data != replacement
        ):
            raise TransportJournalError(
                "transport journal changed during atomic lifecycle transition"
            )
        os.unlink(staging_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
        if not exchanged:
            # A failed pre-exchange staging file is itself a fail-closed marker.
            # Remove it only when the still-current final journal is exactly the
            # generation the caller expected.
            try:
                current = _read_stable_regular(
                    path,
                    maximum=JOURNAL_MAX_BYTES,
                    expected_mode=JOURNAL_MODE,
                )
            except Exception:
                current = None
            if (
                current is not None
                and current.data == expected
                and (
                    int(current.metadata.st_dev),
                    int(current.metadata.st_ino),
                    int(current.metadata.st_ctime_ns),
                )
                == (expected_device, expected_inode, expected_ctime_ns)
            ):
                try:
                    staging.unlink()
                except FileNotFoundError:
                    pass


def replace_exact_source_receipt_generation(
    path: Path,
    *,
    expected_bytes: bytes,
    replacement_bytes: bytes,
    expected_device: int,
    expected_inode: int,
    expected_ctime_ns: int,
    mutation_authority: TransactionMutationAuthority | None = None,
) -> None:
    """Replace one exact owned 0600 source-receipt generation.

    A stable pre-exchange read proves bytes, device, inode and change time.  The
    displaced entry then proves bytes/device/inode across the atomic exchange;
    any mismatch leaves staging as a restart-visible fail-closed marker.
    """

    require_transaction_mutation_authority(
        mutation_authority,
        operation="exact source receipt generation replacement",
    )
    if (
        type(expected_bytes) is not bytes
        or not expected_bytes
        or len(expected_bytes) > JOURNAL_MAX_BYTES
        or type(replacement_bytes) is not bytes
        or not replacement_bytes
        or len(replacement_bytes) > JOURNAL_MAX_BYTES
        or type(expected_device) is not int
        or expected_device < 0
        or type(expected_inode) is not int
        or expected_inode <= 0
        or type(expected_ctime_ns) is not int
        or expected_ctime_ns < 0
    ):
        raise TransportJournalError(
            "exact source receipt generation authority is invalid"
        )
    try:
        _replace_exact(
            Path(path),
            expected=expected_bytes,
            replacement=replacement_bytes,
            expected_device=expected_device,
            expected_inode=expected_inode,
            expected_ctime_ns=expected_ctime_ns,
        )
    except Exception as exc:
        raise BoundSourceReceiptTransitionError(
            "exact source receipt generation replacement did not complete"
        ) from exc


def replace_exact_source_receipt_document(
    path: Path,
    *,
    expected_bytes: bytes,
    replacement_bytes: bytes,
    mutation_authority: TransactionMutationAuthority | None = None,
) -> None:
    """Replace the exact source document observed by this operation.

    The stable read establishes bytes and generation identity before the
    exchange.  A peer replacement after that observation is rejected by the
    generation-bound exchange rather than overwritten blindly.
    """

    require_transaction_mutation_authority(
        mutation_authority,
        operation="exact source receipt document replacement",
    )
    try:
        current = _read_stable_regular(
            Path(path),
            maximum=JOURNAL_MAX_BYTES,
            expected_mode=JOURNAL_MODE,
        )
    except Exception as exc:
        raise BoundSourceReceiptTransitionError(
            "exact source receipt document could not be observed"
        ) from exc
    if current.data != expected_bytes:
        raise BoundSourceReceiptTransitionError(
            "exact source receipt document changed before replacement"
        )
    replace_exact_source_receipt_generation(
        Path(path),
        expected_bytes=expected_bytes,
        replacement_bytes=replacement_bytes,
        expected_device=int(current.metadata.st_dev),
        expected_inode=int(current.metadata.st_ino),
        expected_ctime_ns=int(current.metadata.st_ctime_ns),
        mutation_authority=mutation_authority,
    )


def publish_exact_source_receipt_document(
    path: Path,
    *,
    receipt_bytes: bytes,
    mutation_authority: TransactionMutationAuthority | None = None,
) -> None:
    """Publish one new exact source receipt without overwriting a peer."""

    require_transaction_mutation_authority(
        mutation_authority,
        operation="exact source receipt document publication",
    )
    if (
        type(receipt_bytes) is not bytes
        or not receipt_bytes
        or len(receipt_bytes) > JOURNAL_MAX_BYTES
    ):
        raise BoundSourceReceiptTransitionError(
            "exact source receipt publication bytes are invalid"
        )
    try:
        _publish_new(Path(path), receipt_bytes)
        current = _read_stable_regular(
            Path(path),
            maximum=JOURNAL_MAX_BYTES,
            expected_mode=JOURNAL_MODE,
        )
    except FileExistsError:
        raise
    except Exception as exc:
        raise BoundSourceReceiptTransitionError(
            "exact source receipt document publication did not complete"
        ) from exc
    if current.data != receipt_bytes:
        raise BoundSourceReceiptTransitionError(
            "exact source receipt changed during publication acknowledgement"
        )


def replace_bound_source_receipt(
    binding: SourceReceiptBinding,
    replacement_bytes: bytes,
    *,
    mutation_authority: TransactionMutationAuthority | None = None,
) -> None:
    """Replace exactly the inode bound by a confirmed transport journal.

    ``RENAME_EXCHANGE`` preserves the displaced namespace entry for inspection.
    If a peer replaced the source after binding, the transition fails and the
    surviving staging entry remains a restart-visible journal auxiliary rather
    than silently accepting or destroying the changed authority.
    """

    require_transaction_mutation_authority(
        mutation_authority,
        operation="bound source receipt replacement",
    )
    if not replacement_bytes or len(replacement_bytes) > JOURNAL_MAX_BYTES:
        raise TransportJournalError("replacement source receipt bytes are invalid")
    try:
        replace_exact_source_receipt_generation(
            Path(binding.receipt_path),
            expected_bytes=binding.receipt_bytes,
            replacement_bytes=replacement_bytes,
            expected_device=binding.receipt_device,
            expected_inode=binding.receipt_inode,
            expected_ctime_ns=binding.receipt_ctime_ns,
            mutation_authority=mutation_authority,
        )
    except BoundSourceReceiptTransitionError as exc:
        raise BoundSourceReceiptTransitionError(
            "bound source receipt promotion did not complete exactly"
        ) from exc


def _validate_document(
    value: dict[str, Any],
    *,
    expected_kind: str = "mrsMThatcher_remote_write_transport_journal",
) -> None:
    required = {
        "schema_version",
        "document_kind",
        "transaction_id",
        "lifecycle_state",
        "lane",
        "request_method",
        "request_path",
        "remote_payload",
        "remote_payload_sha256",
        "source_receipt",
        "source_validation",
        "remote_post_id",
        "confirmation_epoch",
    }
    if set(value) != required:
        raise TransportJournalError("transport journal fields are invalid")
    if (
        type(value.get("schema_version")) is not int
        or value.get("schema_version") != JOURNAL_SCHEMA_VERSION
        or value.get("document_kind") != expected_kind
        or value.get("lifecycle_state") not in {"prepared", "attempting", "confirmed"}
        or type(value.get("lane")) is not str
        or not _LANE_RE.fullmatch(value["lane"])
        or value.get("request_method") != "POST"
        or value.get("request_path") != "/2/tweets"
        or not isinstance(value.get("remote_payload"), dict)
        or type(value.get("remote_payload_sha256")) is not str
        or not _SHA256_RE.fullmatch(value["remote_payload_sha256"])
        or payload_sha256(value["remote_payload"]) != value["remote_payload_sha256"]
    ):
        raise TransportJournalError("transport journal semantics are invalid")
    source = value.get("source_receipt")
    if (
        not isinstance(source, dict)
        or set(source) != {
            "basename",
            "device",
            "inode",
            "ctime_ns",
            "size",
            "sha256",
        }
        or not isinstance(source.get("basename"), str)
        or not source["basename"]
        or source["basename"] in {".", ".."}
        or Path(source["basename"]).name != source["basename"]
        or type(source.get("device")) is not int
        or type(source.get("inode")) is not int
        or type(source.get("ctime_ns")) is not int
        or type(source.get("size")) is not int
        or source["device"] < 0
        or source["inode"] <= 0
        or source["ctime_ns"] < 0
        or source["size"] <= 0
        or type(source.get("sha256")) is not str
        or not _SHA256_RE.fullmatch(source["sha256"])
    ):
        raise TransportJournalError("transport journal source receipt is invalid")
    source_validation = value.get("source_validation")
    if (
        not isinstance(source_validation, dict)
        or set(source_validation) != {
            "validator_id",
            "receipt_sha256",
            "payload_sha256",
        }
        or type(source_validation.get("validator_id")) is not str
        or not _VALIDATOR_ID_RE.fullmatch(source_validation["validator_id"])
        or type(source_validation.get("receipt_sha256")) is not str
        or type(source_validation.get("payload_sha256")) is not str
        or source_validation.get("receipt_sha256") != source["sha256"]
        or source_validation.get("payload_sha256")
        != value["remote_payload_sha256"]
    ):
        raise TransportJournalError("transport source validation binding is invalid")
    expected_id = hashlib.sha256(
        b"mrsMThatcher-transport-journal-v2\0"
        + str(value["lane"]).encode("utf-8")
        + b"\0"
        + str(source["sha256"]).encode("ascii")
        + b"\0"
        + str(value["remote_payload_sha256"]).encode("ascii")
        + b"\0"
        + str(source_validation["validator_id"]).encode("utf-8")
    ).hexdigest()
    if value.get("transaction_id") != expected_id:
        raise TransportJournalError("transport journal transaction ID is invalid")
    remote_post_id = value.get("remote_post_id")
    confirmation_epoch = value.get("confirmation_epoch")
    if value["lifecycle_state"] == "confirmed":
        if (
            type(remote_post_id) is not str
            or not _POST_ID_RE.fullmatch(remote_post_id)
            or type(confirmation_epoch) is not int
            or confirmation_epoch < 0
        ):
            raise TransportJournalError("confirmed journal has no valid post ID")
    elif remote_post_id is not None or confirmation_epoch is not None:
        raise TransportJournalError("unconfirmed journal contains confirmation data")


def _snapshot(
    path: Path,
    *,
    expected_kind: str = "mrsMThatcher_remote_write_transport_journal",
) -> JournalSnapshot:
    inspected = _read_stable_regular(
        path,
        maximum=JOURNAL_MAX_BYTES,
        expected_mode=JOURNAL_MODE,
    )
    document = _parse_canonical(inspected.data, label="transport journal")
    _validate_document(document, expected_kind=expected_kind)
    return JournalSnapshot(
        document=document,
        data=inspected.data,
        device=int(inspected.metadata.st_dev),
        inode=int(inspected.metadata.st_ino),
        ctime_ns=int(inspected.metadata.st_ctime_ns),
        sha256=hashlib.sha256(inspected.data).hexdigest(),
    )


def _required_snapshot(path: Path) -> JournalSnapshot:
    """Read a journal generation whose disappearance is itself a fault."""

    try:
        return _snapshot(path)
    except FileNotFoundError as exc:
        raise TransportJournalError(
            "required transport journal disappeared"
        ) from exc


def _required_fence_snapshot(path: Path) -> JournalSnapshot:
    """Read the immutable companion whose disappearance is itself a fault."""

    try:
        return _snapshot(
            path,
            expected_kind="mrsMThatcher_remote_write_transport_fence",
        )
    except FileNotFoundError as exc:
        raise TransportJournalError(
            "required transport fence disappeared"
        ) from exc


def _unsafe_auxiliary_names(path: Path) -> list[str]:
    try:
        names = os.listdir(path.parent)
    except OSError as exc:
        raise TransportJournalError("cannot inspect transport journal directory") from exc
    return sorted(
        name
        for name in names
        if name.startswith(JOURNAL_STAGING_PREFIX)
        or name.startswith(JOURNAL_RETIREMENT_PREFIX)
    )


def _documents_share_transaction_identity(
    journal: Mapping[str, Any],
    fence: Mapping[str, Any],
) -> bool:
    fields = (
        "transaction_id",
        "lane",
        "request_method",
        "request_path",
        "remote_payload",
        "remote_payload_sha256",
        "source_receipt",
        "source_validation",
    )
    return all(journal.get(field) == fence.get(field) for field in fields)


def inspect_transport_state(path: Path) -> TransportJournalState:
    """Inspect all transaction names in sorted, deterministic order.

    Unlike :func:`inspect_transport_journal`, this diagnostic API does not
    discard useful state merely because retirement is in progress.  Invalid
    objects are represented by stable error codes and remain blocking.
    """

    path = Path(path)
    try:
        names = sorted(os.listdir(path.parent))
    except OSError:
        return TransportJournalState(
            classification="directory_unavailable",
            blocking=True,
            journal=None,
            fence=None,
            staging_names=(),
            retirement_guard_names=(),
            errors=("directory_unavailable",),
        )
    staging = tuple(
        name for name in names if name.startswith(JOURNAL_STAGING_PREFIX)
    )
    guards = tuple(
        name for name in names if name.startswith(JOURNAL_RETIREMENT_PREFIX)
    )
    errors: list[str] = []
    try:
        journal = _snapshot(path)
    except FileNotFoundError:
        journal = None
    except Exception:
        journal = None
        errors.append("journal_invalid")
    fence_path = fence_path_for_journal(path)
    try:
        fence = _snapshot(
            fence_path,
            expected_kind="mrsMThatcher_remote_write_transport_fence",
        )
    except FileNotFoundError:
        fence = None
    except Exception:
        fence = None
        errors.append("fence_invalid")
    if journal is not None and fence is not None and not _documents_share_transaction_identity(
        journal.document,
        fence.document,
    ):
        errors.append("pair_identity_mismatch")
    if staging:
        classification = "lifecycle_transition_in_progress"
    elif guards:
        classification = "retirement_in_progress"
    elif errors:
        classification = "invalid"
    elif journal is None and fence is None:
        classification = "clear"
    elif journal is None or fence is None:
        classification = "incomplete_pair"
    else:
        classification = f"{journal.document['lifecycle_state']}_pair"
    blocking = bool(staging or guards or errors or journal is not None or fence is not None)
    return TransportJournalState(
        classification=classification,
        blocking=blocking,
        journal=journal,
        fence=fence,
        staging_names=staging,
        retirement_guard_names=guards,
        errors=tuple(sorted(errors)),
    )


def inspect_transport_journal(path: Path) -> JournalSnapshot | None:
    """Return one valid journal, or fail on any torn transition pathname."""

    path = Path(path)
    auxiliaries = _unsafe_auxiliary_names(path)
    if auxiliaries:
        raise TransportJournalError(
            "unfinished transport-journal transition blocks remote writes"
        )
    try:
        return _snapshot(path)
    except FileNotFoundError:
        return None


def transport_journal_is_blocking(path: Path) -> bool:
    """Treat either durable companion, invalid state, or a torn transition as a barrier."""

    return inspect_transport_state(path).blocking


def transport_journal_has_valid_restart_barrier(path: Path) -> bool:
    """Return whether at least one strict durable transaction object is readable.

    This predicate is deliberately narrower than
    :func:`transport_journal_is_blocking`.  An unavailable directory, malformed
    object, or unexplained auxiliary name must block remote work, but it is not
    evidence strong enough to release a retained confirmed-post signal guard.
    A strict journal or immutable fence snapshot is independently sufficient:
    either pathname remains a restart-visible global barrier even when its
    companion is missing or inconsistent.
    """

    state = inspect_transport_state(Path(path))
    return state.journal is not None or state.fence is not None


def begin_transport_transaction(
    *,
    receipt_path: Path,
    expected_receipt: Mapping[str, Any] | None = None,
    expected_receipt_bytes: bytes | None = None,
    lane: str | None = None,
    payload: Mapping[str, Any] | None = None,
    source_binding: SourceReceiptBinding | None = None,
    source_validator_id: str | None = None,
    source_validator: (
        Callable[[str, Mapping[str, Any], Mapping[str, Any]], bool] | None
    ) = None,
) -> TransportAuthority:
    """Publish a payload-bound restart barrier from one exact lane receipt."""

    receipt_path = Path(receipt_path)
    if source_binding is None:
        if (
            expected_receipt is None
            or lane is None
            or payload is None
            or source_validator_id is None
            or source_validator is None
        ):
            raise TransportJournalError(
                "a lane-owned semantic source binding is required"
            )
        source_binding = bind_transport_source(
            receipt_path=receipt_path,
            expected_receipt=expected_receipt,
            expected_receipt_bytes=expected_receipt_bytes,
            lane=lane,
            payload=payload,
            validator_id=source_validator_id,
            validator=source_validator,
        )
    elif any(
        item is not None
        for item in (
            expected_receipt,
            expected_receipt_bytes,
            lane,
            payload,
            source_validator_id,
            source_validator,
        )
    ):
        raise TransportJournalError(
            "source binding cannot be combined with unbound source arguments"
        )
    lane = source_binding.lane
    payload_value = source_binding.request.payload()
    if (
        receipt_path.absolute() != Path(source_binding.receipt_path)
        or source_binding.request.method != "POST"
        or source_binding.request.request_path != "/2/tweets"
    ):
        raise TransportJournalError("source binding is not for this request")
    binding_key = _source_binding_key(source_binding)
    with _authority_lock:
        if binding_key not in _issued_source_bindings:
            raise TransportJournalError(
                "source binding was not issued by semantic validation"
            )
    journal_path = journal_path_for_receipt(receipt_path)
    if transport_journal_is_blocking(journal_path):
        raise TransportJournalError("another transport transaction is unresolved")
    try:
        receipt = _read_stable_regular(receipt_path, maximum=JOURNAL_MAX_BYTES)
    except FileNotFoundError as exc:
        raise TransportJournalError("source receipt is not durably present") from exc
    if (
        receipt.data != source_binding.receipt_bytes
        or hashlib.sha256(receipt.data).hexdigest()
        != source_binding.receipt_sha256
        or (
            int(receipt.metadata.st_dev),
            int(receipt.metadata.st_ino),
            int(receipt.metadata.st_ctime_ns),
        )
        != (
            source_binding.receipt_device,
            source_binding.receipt_inode,
            source_binding.receipt_ctime_ns,
        )
        or len(receipt.data) != source_binding.receipt_size
    ):
        raise TransportJournalError("source receipt changed after semantic validation")
    receipt_hash = hashlib.sha256(receipt.data).hexdigest()
    payload_hash = source_binding.request.payload_sha256
    transaction_id = hashlib.sha256(
        b"mrsMThatcher-transport-journal-v2\0"
        + str(lane).encode("utf-8")
        + b"\0"
        + receipt_hash.encode("ascii")
        + b"\0"
        + payload_hash.encode("ascii")
        + b"\0"
        + source_binding.validator_id.encode("utf-8")
    ).hexdigest()
    document: dict[str, Any] = {
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "document_kind": "mrsMThatcher_remote_write_transport_journal",
        "transaction_id": transaction_id,
        "lifecycle_state": "prepared",
        "lane": str(lane),
        "request_method": "POST",
        "request_path": "/2/tweets",
        "remote_payload": payload_value,
        "remote_payload_sha256": payload_hash,
        "source_receipt": {
            "basename": receipt_path.name,
            "device": int(receipt.metadata.st_dev),
            "inode": int(receipt.metadata.st_ino),
            "ctime_ns": int(receipt.metadata.st_ctime_ns),
            "size": len(receipt.data),
            "sha256": receipt_hash,
        },
        "source_validation": {
            "validator_id": source_binding.validator_id,
            "receipt_sha256": receipt_hash,
            "payload_sha256": payload_hash,
        },
        "remote_post_id": None,
        "confirmation_epoch": None,
    }
    data = canonical_json_bytes(document)
    _publish_new(journal_path, data)
    snapshot = _required_snapshot(journal_path)
    if snapshot.data != data:
        raise TransportJournalError("published transport journal changed")
    fence_path = fence_path_for_journal(journal_path)
    fence_document = {
        **document,
        "document_kind": "mrsMThatcher_remote_write_transport_fence",
    }
    fence_data = canonical_json_bytes(fence_document)
    _publish_new(fence_path, fence_data)
    fence = _required_fence_snapshot(fence_path)
    if fence.data != fence_data:
        raise TransportJournalError("published transport fence changed")
    authority = TransportAuthority(
        transaction_id=transaction_id,
        journal_path=str(journal_path.absolute()),
        journal_sha256=snapshot.sha256,
        journal_device=snapshot.device,
        journal_inode=snapshot.inode,
        journal_ctime_ns=snapshot.ctime_ns,
        fence_path=str(fence_path.absolute()),
        fence_sha256=fence.sha256,
        fence_device=fence.device,
        fence_inode=fence.inode,
        fence_ctime_ns=fence.ctime_ns,
        payload_sha256=payload_hash,
        lane=str(lane),
        source_receipt_basename=receipt_path.name,
        source_validator_id=source_binding.validator_id,
        lifecycle_state="prepared",
    )
    with _authority_lock:
        _issued_source_bindings.discard(binding_key)
        _issued_untransmitted_authorities.add(
            (authority.journal_path, authority.transaction_id, "prepared")
        )
    return authority


def arm_transport_transaction(
    path: Path,
    authority: TransportAuthority,
    *,
    mutation_authority: TransactionMutationAuthority | None = None,
) -> TransportAuthority:
    """Make the durable outcome explicitly ambiguous before transport."""

    require_transaction_mutation_authority(
        mutation_authority,
        operation="transport journal arming",
    )
    transaction_key = (authority.journal_path, authority.transaction_id)
    prepared_key = (*transaction_key, "prepared")
    with _authority_lock:
        if (
            prepared_key not in _issued_untransmitted_authorities
            or transaction_key in _aborting_transactions
            or transaction_key in _transitioning_transactions
        ):
            raise TransportJournalError("prepared transport authority was not issued")
        _issued_untransmitted_authorities.discard(prepared_key)
        _transitioning_transactions.add(transaction_key)
    try:
        snapshot = _required_snapshot(Path(path))
        fence = _required_fence_snapshot(Path(authority.fence_path))
    except Exception:
        with _authority_lock:
            _transitioning_transactions.discard(transaction_key)
        raise
    if (
        authority.lifecycle_state != "prepared"
        or snapshot.sha256 != authority.journal_sha256
        or (snapshot.device, snapshot.inode, snapshot.ctime_ns)
        != (
            authority.journal_device,
            authority.journal_inode,
            authority.journal_ctime_ns,
        )
        or snapshot.document["transaction_id"] != authority.transaction_id
        or snapshot.document["lifecycle_state"] != "prepared"
        or fence.sha256 != authority.fence_sha256
        or (fence.device, fence.inode, fence.ctime_ns)
        != (
            authority.fence_device,
            authority.fence_inode,
            authority.fence_ctime_ns,
        )
        or fence.document["transaction_id"] != authority.transaction_id
    ):
        with _authority_lock:
            _transitioning_transactions.discard(transaction_key)
        raise TransportJournalError("prepared transport authority is stale")
    replacement = {**snapshot.document, "lifecycle_state": "attempting"}
    replacement_data = canonical_json_bytes(replacement)
    try:
        _replace_exact(
            Path(path),
            expected=snapshot.data,
            replacement=replacement_data,
            expected_device=snapshot.device,
            expected_inode=snapshot.inode,
            expected_ctime_ns=snapshot.ctime_ns,
        )
        updated = _required_snapshot(Path(path))
        if updated.data != replacement_data:
            raise TransportJournalError("armed transport journal changed")
    finally:
        with _authority_lock:
            _transitioning_transactions.discard(transaction_key)
    updated_authority = TransportAuthority(
        transaction_id=authority.transaction_id,
        journal_path=authority.journal_path,
        journal_sha256=updated.sha256,
        journal_device=updated.device,
        journal_inode=updated.inode,
        journal_ctime_ns=updated.ctime_ns,
        fence_path=authority.fence_path,
        fence_sha256=authority.fence_sha256,
        fence_device=authority.fence_device,
        fence_inode=authority.fence_inode,
        fence_ctime_ns=authority.fence_ctime_ns,
        payload_sha256=authority.payload_sha256,
        lane=authority.lane,
        source_receipt_basename=authority.source_receipt_basename,
        source_validator_id=authority.source_validator_id,
        lifecycle_state="attempting",
    )
    with _authority_lock:
        _issued_untransmitted_authorities.add(
            (authority.journal_path, authority.transaction_id, "attempting")
        )
    return updated_authority


def consume_transport_authority(
    path: Path,
    authority: TransportAuthority,
    *,
    method: str,
    request_path: str,
    payload: Mapping[str, Any],
    expected_receipt_path: Path | None = None,
) -> None:
    """Validate and consume one exact authority immediately before transport."""

    snapshot = _required_snapshot(Path(path))
    fence_path = Path(authority.fence_path)
    fence = _required_fence_snapshot(fence_path)
    payload_hash = payload_sha256(payload)
    canonical_path = Path(path).absolute()
    if expected_receipt_path is not None:
        expected_receipt_path = Path(expected_receipt_path)
        if (
            canonical_path != journal_path_for_receipt(expected_receipt_path).absolute()
            or fence_path.absolute()
            != fence_path_for_journal(canonical_path).absolute()
            or authority.source_receipt_basename != expected_receipt_path.name
            or snapshot.document["source_receipt"]["basename"]
            != expected_receipt_path.name
        ):
            raise TransportJournalError(
                "transport authority is not anchored to the canonical lane receipt"
            )
    if (
        authority.lifecycle_state != "attempting"
        or snapshot.sha256 != authority.journal_sha256
        or (snapshot.device, snapshot.inode, snapshot.ctime_ns)
        != (
            authority.journal_device,
            authority.journal_inode,
            authority.journal_ctime_ns,
        )
        or snapshot.document["transaction_id"] != authority.transaction_id
        or snapshot.document["lifecycle_state"] != "attempting"
        or snapshot.document["request_method"] != str(method).upper()
        or snapshot.document["request_path"] != request_path
        or snapshot.document["remote_payload"] != dict(payload)
        or snapshot.document["remote_payload_sha256"] != payload_hash
        or authority.payload_sha256 != payload_hash
        or snapshot.document["source_validation"]["validator_id"]
        != authority.source_validator_id
        or fence.sha256 != authority.fence_sha256
        or (fence.device, fence.inode, fence.ctime_ns)
        != (
            authority.fence_device,
            authority.fence_inode,
            authority.fence_ctime_ns,
        )
        or fence.document["transaction_id"] != authority.transaction_id
        or fence.document["remote_payload"] != dict(payload)
        or fence.document["source_receipt"]
        != snapshot.document["source_receipt"]
    ):
        raise TransportJournalError("transport authority does not bind this request")
    key = (str(Path(path).absolute()), authority.transaction_id)
    with _authority_lock:
        if key in _consumed_authorities:
            raise TransportJournalError("transport authority was already consumed")
        issued_key = (
            authority.journal_path,
            authority.transaction_id,
            "attempting",
        )
        if issued_key not in _issued_untransmitted_authorities:
            raise TransportJournalError(
                "transport authority was not issued in this process"
            )
        _issued_untransmitted_authorities.discard(issued_key)
        _consumed_authorities.add(key)


def confirm_transport_transaction(
    path: Path,
    authority: TransportAuthority,
    *,
    mutation_authority: TransactionMutationAuthority | None = None,
    post_id: str,
    confirmation_epoch: int,
) -> JournalSnapshot:
    """Bind a valid remote post ID before returning control to the lane."""

    require_transaction_mutation_authority(
        mutation_authority,
        operation="transport journal confirmation",
    )
    if not _POST_ID_RE.fullmatch(str(post_id or "")):
        raise TransportJournalError("remote confirmation has no valid post ID")
    if type(confirmation_epoch) is not int or confirmation_epoch < 0:
        raise TransportJournalError("remote confirmation epoch is invalid")
    snapshot = _required_snapshot(Path(path))
    fence = _required_fence_snapshot(Path(authority.fence_path))
    key = (str(Path(path).absolute()), authority.transaction_id)
    if (
        authority.lifecycle_state != "attempting"
        or snapshot.sha256 != authority.journal_sha256
        or (snapshot.device, snapshot.inode, snapshot.ctime_ns)
        != (
            authority.journal_device,
            authority.journal_inode,
            authority.journal_ctime_ns,
        )
        or snapshot.document["transaction_id"] != authority.transaction_id
        or snapshot.document["lifecycle_state"] != "attempting"
        or fence.sha256 != authority.fence_sha256
        or (fence.device, fence.inode, fence.ctime_ns)
        != (
            authority.fence_device,
            authority.fence_inode,
            authority.fence_ctime_ns,
        )
        or fence.document["transaction_id"] != authority.transaction_id
    ):
        raise TransportJournalError("attempting transport authority is stale")
    with _authority_lock:
        if key not in _consumed_authorities:
            raise TransportJournalError(
                "transport authority was not consumed at the request boundary"
            )
    replacement = {
        **snapshot.document,
        "lifecycle_state": "confirmed",
        "remote_post_id": str(post_id),
        "confirmation_epoch": confirmation_epoch,
    }
    replacement_data = canonical_json_bytes(replacement)
    _replace_exact(
        Path(path),
        expected=snapshot.data,
        replacement=replacement_data,
        expected_device=snapshot.device,
        expected_inode=snapshot.inode,
        expected_ctime_ns=snapshot.ctime_ns,
    )
    updated = _required_snapshot(Path(path))
    if updated.data != replacement_data:
        raise TransportJournalError("confirmed transport journal changed")
    return updated


def inspect_confirmed_transport_transaction(
    path: Path,
) -> ConfirmedTransportDetails:
    """Return strict durable details for restart-time receipt promotion."""

    path = Path(path)
    state = inspect_transport_state(path)
    if (
        state.errors
        or state.staging_names
        or state.retirement_guard_names
        or state.journal is None
        or state.fence is None
        or state.journal.document["lifecycle_state"] != "confirmed"
        or not _documents_share_transaction_identity(
            state.journal.document,
            state.fence.document,
        )
    ):
        raise TransportJournalError(
            "transport transaction is not one intact confirmed pair"
        )
    document = state.journal.document
    payload_bytes = canonical_json_bytes(document["remote_payload"])
    return ConfirmedTransportDetails(
        transaction_id=str(document["transaction_id"]),
        lane=str(document["lane"]),
        post_id=str(document["remote_post_id"]),
        confirmation_epoch=int(document["confirmation_epoch"]),
        source_receipt_basename=str(document["source_receipt"]["basename"]),
        source_receipt_sha256=str(document["source_receipt"]["sha256"]),
        source_validator_id=str(document["source_validation"]["validator_id"]),
        payload_bytes=payload_bytes,
        payload_sha256=str(document["remote_payload_sha256"]),
        journal_path=str(path.absolute()),
        journal_sha256=state.journal.sha256,
        journal_device=state.journal.device,
        journal_inode=state.journal.inode,
        journal_ctime_ns=state.journal.ctime_ns,
    )


def verify_confirmed_transport_source_lineage(
    *,
    receipt_path: Path,
    expected_source_receipt_bytes: bytes,
    lane: str,
    post_id: str,
    validator_id: str = LANE_SOURCE_VALIDATOR_ID,
) -> ConfirmedTransportDetails:
    """Prove source lineage before a confirmed receipt mutates local state.

    A confirmed lane receipt is a derived recovery representation.  It may be
    applied only when the still-durable transport pair binds the exact
    pre-transport receipt from which that representation was derived.  This
    check is intentionally read-only; retirement remains a later operation
    after all protected local effects are durable.
    """

    receipt_path = Path(receipt_path)
    if (
        type(expected_source_receipt_bytes) is not bytes
        or not expected_source_receipt_bytes
        or len(expected_source_receipt_bytes) > JOURNAL_MAX_BYTES
    ):
        raise TransportJournalError("confirmed source lineage bytes are invalid")
    details = inspect_confirmed_transport_transaction(
        journal_path_for_receipt(receipt_path)
    )
    if (
        details.lane != str(lane)
        or details.post_id != str(post_id)
        or details.source_receipt_basename != receipt_path.name
        or details.source_receipt_sha256
        != hashlib.sha256(expected_source_receipt_bytes).hexdigest()
        or details.source_validator_id != str(validator_id)
    ):
        raise TransportJournalError(
            "confirmed transport does not bind the expected source receipt"
        )
    return details


def bind_confirmed_transport_source(
    *,
    journal_path: Path,
    receipt_path: Path,
    validator_id: str,
    validator: Callable[[str, Mapping[str, Any], Mapping[str, Any]], bool],
) -> ConfirmedSourceRecovery:
    """Revalidate the original receipt before promoting it after restart."""

    details = inspect_confirmed_transport_transaction(journal_path)
    receipt_path = Path(receipt_path)
    if (
        receipt_path.name != details.source_receipt_basename
        or journal_path_for_receipt(receipt_path).absolute()
        != Path(journal_path).absolute()
        or validator_id != details.source_validator_id
    ):
        raise TransportJournalError("confirmed transport source anchor is invalid")
    receipt = _read_stable_regular(receipt_path, maximum=JOURNAL_MAX_BYTES)
    document = _parse_strict_object_bytes(receipt.data, label="source receipt")
    request = freeze_tweet_request(
        method="POST",
        request_path="/2/tweets",
        payload=details.payload(),
    )
    source = inspect_transport_state(Path(journal_path)).journal
    if (
        source is None
        or source.sha256 != details.journal_sha256
        or (source.device, source.inode, source.ctime_ns)
        != (
            details.journal_device,
            details.journal_inode,
            details.journal_ctime_ns,
        )
    ):
        raise TransportJournalError("confirmed transport journal changed")
    expected = source.document["source_receipt"]
    if (
        hashlib.sha256(receipt.data).hexdigest() != details.source_receipt_sha256
        or (
            int(receipt.metadata.st_dev),
            int(receipt.metadata.st_ino),
            int(receipt.metadata.st_ctime_ns),
        )
        != (
            int(expected["device"]),
            int(expected["inode"]),
            int(expected["ctime_ns"]),
        )
        or len(receipt.data) != int(expected["size"])
    ):
        raise TransportJournalError("confirmed transport source receipt changed")
    try:
        approved = validator(details.lane, dict(document), request.payload())
    except Exception as exc:
        raise TransportJournalError("confirmed source semantic validation failed") from exc
    if approved is not True:
        raise TransportJournalError(
            "confirmed source receipt does not semantically bind payload"
        )
    binding = SourceReceiptBinding(
        receipt_path=str(receipt_path.absolute()),
        receipt_bytes=receipt.data,
        receipt_sha256=details.source_receipt_sha256,
        receipt_device=int(receipt.metadata.st_dev),
        receipt_inode=int(receipt.metadata.st_ino),
        receipt_ctime_ns=int(receipt.metadata.st_ctime_ns),
        receipt_size=len(receipt.data),
        receipt_document=document,
        lane=details.lane,
        request=request,
        validator_id=validator_id,
    )
    return ConfirmedSourceRecovery(details=details, source_binding=binding)


def abort_untransmitted_transport_transaction(
    *,
    source_binding: SourceReceiptBinding,
    authority: TransportAuthority | None = None,
    mutation_authority: TransactionMutationAuthority | None = None,
) -> None:
    """Retire a transaction proven not to have reached transport.

    A ``prepared`` journal is durably pre-transport and can be aborted after
    revalidating its semantic source binding.  An ``attempting`` journal can
    be aborted only by the same process which armed it and before authority
    consumption.  Restarted processes must treat ``attempting`` as ambiguous.
    """

    require_transaction_mutation_authority(
        mutation_authority,
        operation="untransmitted transport journal abort",
    )
    path = journal_path_for_receipt(Path(source_binding.receipt_path))
    state = inspect_transport_state(path)
    if (
        state.errors
        or state.staging_names
        or state.retirement_guard_names
        or state.journal is None
        or state.fence is None
        or not _documents_share_transaction_identity(
            state.journal.document,
            state.fence.document,
        )
    ):
        raise TransportJournalError("untransmitted transaction is not intact")
    document = state.journal.document
    if (
        document["lane"] != source_binding.lane
        or document["remote_payload_sha256"]
        != source_binding.request.payload_sha256
        or document["source_receipt"]["sha256"]
        != source_binding.receipt_sha256
        or document["source_validation"]["validator_id"]
        != source_binding.validator_id
    ):
        raise TransportJournalError("untransmitted source binding is stale")
    current_receipt = _read_stable_regular(
        Path(source_binding.receipt_path),
        maximum=JOURNAL_MAX_BYTES,
    )
    if (
        current_receipt.data != source_binding.receipt_bytes
        or (
            int(current_receipt.metadata.st_dev),
            int(current_receipt.metadata.st_ino),
            int(current_receipt.metadata.st_ctime_ns),
        )
        != (
            source_binding.receipt_device,
            source_binding.receipt_inode,
            source_binding.receipt_ctime_ns,
        )
    ):
        raise TransportJournalError("untransmitted source receipt changed")
    lifecycle = str(document["lifecycle_state"])
    issued_key = (str(path.absolute()), str(document["transaction_id"]), lifecycle)
    transaction_key = (str(path.absolute()), str(document["transaction_id"]))
    with _authority_lock:
        if (
            transaction_key in _transitioning_transactions
            or transaction_key in _aborting_transactions
        ):
            raise TransportJournalError("transport lifecycle transition is in progress")
        if lifecycle == "attempting":
            if (
                authority is None
                or authority.lifecycle_state != "attempting"
                or authority.transaction_id != document["transaction_id"]
                or issued_key not in _issued_untransmitted_authorities
                or (str(path.absolute()), authority.transaction_id)
                in _consumed_authorities
            ):
                raise TransportJournalError(
                    "attempting transaction is not provably untransmitted"
                )
        elif lifecycle != "prepared":
            raise TransportJournalError("confirmed transaction cannot be aborted")
        _issued_untransmitted_authorities.discard(issued_key)
        _aborting_transactions.add(transaction_key)
    directory_fd: int | None = None
    try:
        directory_fd = _open_directory(path.parent)
        os.unlink(path.name, dir_fd=directory_fd)
        os.fsync(directory_fd)
        # The immutable fence remains the restart barrier until journal
        # removal is durable.
        os.unlink(fence_path_for_journal(path).name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        with _authority_lock:
            _aborting_transactions.discard(transaction_key)


def retire_confirmed_transport_transaction(
    *,
    mutation_authority: TransactionMutationAuthority | None = None,
    receipt_path: Path,
    expected_confirmed_receipt: Mapping[str, Any],
    expected_source_receipt_bytes: bytes | None = None,
    expected_current_receipt_bytes: bytes | None = None,
    source_retirement_prepared: bool = False,
    lane: str,
    post_id: str,
) -> None:
    """Retire the journal while an exact confirmed lane receipt remains.

    By default, a hard-link retirement guard preserves the receipt inode while the journal
    is removed.  Any crash leaves either the journal, the guard, or the lane
    receipt visible to the next process.  Before the immutable journal/fence
    pair is retired, ``expected_source_receipt_bytes`` must reproduce the
    exact pre-transport receipt hash recorded by that pair.  This prevents a
    semantically plausible but unrelated confirmed receipt from retiring a
    transaction merely because its lane and remote post ID happen to match.

    The small implicit reconstruction below exists for the original generic
    API shape (``attempting`` -> ``confirmed`` plus ``post_id``) and is itself
    hash-checked.  Production lanes whose confirmed representation is derived
    or otherwise changes shape always supply their exact reconstructed source
    bytes explicitly.

    Production callers may instead set ``source_retirement_prepared`` and
    supply the exact current receipt bytes.  That mode requires the shared
    exact-retirement prepare guard and leaves it untouched as the overlapping
    barrier, avoiding any hard-link mutation of its bound source inode.
    """

    require_transaction_mutation_authority(
        mutation_authority,
        operation="confirmed transport journal retirement",
    )
    receipt_path = Path(receipt_path)
    path = journal_path_for_receipt(receipt_path)
    fence_path = fence_path_for_journal(path)
    state = inspect_transport_state(path)
    if state.errors or state.staging_names:
        raise TransportJournalError("confirmed retirement state is invalid")
    if len(state.retirement_guard_names) > 1:
        raise TransportJournalError("multiple retirement guards are present")
    if source_retirement_prepared:
        if state.retirement_guard_names:
            raise TransportJournalError(
                "legacy journal retirement guard conflicts with prepared source retirement"
            )
        if (
            type(expected_current_receipt_bytes) is not bytes
            or not expected_current_receipt_bytes
        ):
            raise TransportJournalError(
                "prepared source retirement lacks exact current receipt bytes"
            )
        from exact_receipt_retirement import inspect_exact_receipt_retirement

        prepared = inspect_exact_receipt_retirement(
            receipt_path,
            expected_current_receipt_bytes,
        )
        if not prepared.valid or prepared.phase != "prepared":
            raise TransportJournalError(
                "exact source retirement is not durably prepared"
            )

    if expected_source_receipt_bytes is None:
        # Backwards-compatible only for the simple, lossless generic
        # transition exercised by callers which retain every source field.
        reconstructed = dict(expected_confirmed_receipt)
        if (
            reconstructed.get("lifecycle_state") != "confirmed"
            or "post_id" not in reconstructed
        ):
            raise TransportJournalError(
                "confirmed retirement lacks exact source-receipt lineage"
            )
        reconstructed["lifecycle_state"] = "attempting"
        reconstructed.pop("post_id", None)
        expected_source_receipt_bytes = canonical_json_bytes(reconstructed)
    if (
        type(expected_source_receipt_bytes) is not bytes
        or not expected_source_receipt_bytes
        or len(expected_source_receipt_bytes) > JOURNAL_MAX_BYTES
    ):
        raise TransportJournalError("confirmed source-receipt lineage is invalid")
    expected_source_sha256 = hashlib.sha256(
        expected_source_receipt_bytes
    ).hexdigest()

    def require_source_lineage(document: Mapping[str, Any], *, label: str) -> None:
        source = document.get("source_receipt")
        source_validation = document.get("source_validation")
        if (
            not isinstance(source, Mapping)
            or not isinstance(source_validation, Mapping)
            or source.get("sha256") != expected_source_sha256
            or source.get("size") != len(expected_source_receipt_bytes)
            or source_validation.get("receipt_sha256") != expected_source_sha256
        ):
            raise TransportJournalError(
                f"confirmed {label} source-receipt lineage does not match"
            )

    if state.journal is not None:
        require_source_lineage(state.journal.document, label="journal")
    if state.fence is not None:
        require_source_lineage(state.fence.document, label="fence")
    if state.journal is None and state.fence is None and not state.retirement_guard_names:
        # Idempotent completion after a previous final directory-fsync error.
        receipt = _read_stable_regular(receipt_path, maximum=JOURNAL_MAX_BYTES)
        if _parse_strict_object_bytes(
            receipt.data,
            label="confirmed receipt",
        ) != dict(expected_confirmed_receipt):
            raise TransportJournalError("confirmed receipt does not match transaction")
        return

    transaction_id: str | None = None
    if state.journal is not None:
        document = state.journal.document
        if (
            document["lifecycle_state"] != "confirmed"
            or document["lane"] != lane
            or document["remote_post_id"] != str(post_id)
            or document["source_receipt"]["basename"] != receipt_path.name
        ):
            raise TransportJournalError("confirmed journal does not match lane receipt")
        transaction_id = str(document["transaction_id"])
    if state.fence is not None:
        fence_document = state.fence.document
        if (
            fence_document["lane"] != lane
            or fence_document["source_receipt"]["basename"] != receipt_path.name
            or (
                transaction_id is not None
                and fence_document["transaction_id"] != transaction_id
            )
        ):
            raise TransportJournalError("transport fence does not match lane receipt")
        transaction_id = str(fence_document["transaction_id"])
    if state.retirement_guard_names:
        guard_name = state.retirement_guard_names[0]
        guard_transaction_id = guard_name[len(JOURNAL_RETIREMENT_PREFIX) :]
        if not _SHA256_RE.fullmatch(guard_transaction_id):
            raise TransportJournalError("retirement guard name is invalid")
        if transaction_id is not None and guard_transaction_id != transaction_id:
            raise TransportJournalError("retirement guard transaction is stale")
        transaction_id = guard_transaction_id
    if transaction_id is None:
        raise TransportJournalError("retirement transaction identity is unavailable")
    guard_name = f"{JOURNAL_RETIREMENT_PREFIX}{transaction_id}"
    guard_path = path.parent / guard_name

    allowed_links = frozenset({1, 2})
    receipt = _read_stable_regular(
        receipt_path,
        maximum=JOURNAL_MAX_BYTES,
        allowed_link_counts=allowed_links,
    )
    parsed = _parse_strict_object_bytes(receipt.data, label="confirmed receipt")
    if parsed != dict(expected_confirmed_receipt):
        raise TransportJournalError("confirmed receipt does not match transaction")

    if source_retirement_prepared:
        from exact_receipt_retirement import inspect_exact_receipt_retirement

        def require_exact_prepared_source(stage: str) -> None:
            prepared = inspect_exact_receipt_retirement(
                receipt_path,
                expected_current_receipt_bytes,
            )
            if not prepared.valid or prepared.phase != "prepared":
                raise TransportJournalError(
                    f"exact source retirement changed during {stage} retirement"
                )

        directory_fd = _open_directory(path.parent)
        try:
            require_exact_prepared_source("fence")
            if state.fence is not None:
                os.unlink(fence_path.name, dir_fd=directory_fd)
                os.fsync(directory_fd)
            require_exact_prepared_source("journal")
            if state.journal is not None:
                os.unlink(path.name, dir_fd=directory_fd)
                os.fsync(directory_fd)
            require_exact_prepared_source("completion")
        finally:
            os.close(directory_fd)
        return

    directory_fd = _open_directory(path.parent)
    try:
        if not state.retirement_guard_names:
            if state.journal is None or state.fence is None:
                raise TransportJournalError(
                    "incomplete transaction has no durable retirement guard"
                )
            os.link(
                receipt_path.name,
                guard_name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
            os.fsync(directory_fd)

        def require_exact_guarded_receipt(stage: str) -> None:
            current = _read_stable_regular(
                receipt_path,
                maximum=JOURNAL_MAX_BYTES,
                allowed_link_counts=frozenset({2}),
            )
            guard = _read_stable_regular(
                guard_path,
                maximum=JOURNAL_MAX_BYTES,
                allowed_link_counts=frozenset({2}),
            )
            if (
                current.data != receipt.data
                or guard.data != receipt.data
                or (current.metadata.st_dev, current.metadata.st_ino)
                != (receipt.metadata.st_dev, receipt.metadata.st_ino)
                or (guard.metadata.st_dev, guard.metadata.st_ino)
                != (receipt.metadata.st_dev, receipt.metadata.st_ino)
            ):
                raise TransportJournalError(
                    f"receipt changed during {stage} retirement"
                )

        require_exact_guarded_receipt("journal")
        if state.journal is not None:
            os.unlink(path.name, dir_fd=directory_fd)
            os.fsync(directory_fd)
        require_exact_guarded_receipt("fence")
        if state.fence is not None:
            os.unlink(fence_path.name, dir_fd=directory_fd)
            os.fsync(directory_fd)
        require_exact_guarded_receipt("guard")
        os.unlink(guard_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def reset_consumed_authorities_for_tests() -> None:
    """Clear process-only replay state for isolated tests."""

    with _authority_lock:
        _consumed_authorities.clear()
        _issued_untransmitted_authorities.clear()
        _issued_source_bindings.clear()
        _transitioning_transactions.clear()
        _aborting_transactions.clear()

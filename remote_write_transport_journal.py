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
import ipaddress
import json
import math
import os
import re
import secrets
import stat
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import requests

from transaction_mutation_authority import (
    TransactionMutationAuthority,
    require_transaction_mutation_authority,
)
JOURNAL_BASENAME = "remote_write_transport_journal.json"
FENCE_BASENAME = "remote_write_transport_fence.json"
JOURNAL_SCHEMA_VERSION = 2
JOURNAL_MODE = 0o600
JOURNAL_MAX_BYTES = 128 * 1024
MAX_FILESYSTEM_IDENTITY_INTEGER = (1 << 64) - 1
MAX_FILESYSTEM_TIMESTAMP_NS = (1 << 64) - 1
MIN_CONFIRMATION_EPOCH = 1_500_000_000
MAX_CONFIRMATION_EPOCH = 4_102_444_800
JOURNAL_STAGING_PREFIX = f".{JOURNAL_BASENAME}.transition."
JOURNAL_RETIREMENT_PREFIX = f".{JOURNAL_BASENAME}.retirement-guard."
LANE_SOURCE_VALIDATOR_ID = "mrs-lane-source-binding-v2"
EXTERNAL_CONFIRMATION_BINDING_SCHEMA_VERSION = 1
EXTERNAL_CONFIRMATION_BINDING_KIND = (
    "mrsMThatcher_external_transport_confirmation_binding"
)
_RENAME_EXCHANGE = 2
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_LANE_RE = re.compile(r"[a-z][a-z0-9_.:-]{0,79}")
_POST_ID_RE = re.compile(r"\d{1,30}")
_MEDIA_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_VALIDATOR_ID_RE = re.compile(r"[a-z][a-z0-9_.:-]{2,159}")
_AUDIT_BASENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,239}")
_JOURNAL_STAGING_NAME_RE = re.compile(
    re.escape(JOURNAL_STAGING_PREFIX) + r"[0-9a-f]{32}"
)
_TEST_MODE_AT_IMPORT = os.getenv("MRS_TEST_MODE") == "1"
_consumed_authorities: set[tuple[str, str]] = set()
_consumed_authority_objects: dict[tuple[str, str], "TransportAuthority"] = {}
_issued_untransmitted_authorities: set[tuple[str, str, str]] = set()
_issued_untransmitted_authority_objects: dict[
    tuple[str, str, str], "TransportAuthority"
] = {}
_consumed_x_responses: dict[
    int,
    tuple[
        "_ConsumedXResponse",
        tuple[str, str],
        "TransportAuthority",
        object,
        "SourceReceiptBinding",
        int,
        str,
    ],
] = {}
_bound_x_requests: dict[
    int,
    tuple[
        "_BoundXRequestAuthority",
        "TransportAuthority",
        "SourceReceiptBinding",
        str,
        object,
        object,
        str,
    ],
] = {}
_bound_x_request_transactions: dict[tuple[str, str], int] = {}
_configured_x_request_install_record: (
    _ConfiguredXRequestInstallRecord | None
) = None
_issued_source_bindings: set[
    tuple[str, str, int, int, int, str, str, str]
] = set()
_issued_source_binding_objects: dict[
    tuple[str, str, int, int, int, str, str, str], "SourceReceiptBinding"
] = {}
_transaction_source_bindings: dict[
    tuple[str, str], "SourceReceiptBinding"
] = {}
_transitioning_transactions: set[tuple[str, str]] = set()
_aborting_transactions: set[tuple[str, str]] = set()
_authority_lock = threading.Lock()


class TransportJournalError(RuntimeError):
    """A transport journal is missing, unsafe, conflicting, or stale."""


class BoundSourceReceiptTransitionError(TransportJournalError):
    """An identity-bound source promotion did not complete exactly."""


class _ConsumedXResponse:
    """Opaque proof of one actual response to an exact consumed request."""

    __slots__ = (
        "__pid",
        "__transaction_key",
        "__authority_identity",
        "__response_identity",
        "__status_code",
        "__body_sha256",
    )

    def __init__(
        self,
        *,
        transaction_key: tuple[str, str],
        authority_identity: int,
        response_identity: int,
        status_code: int,
        body_sha256: str,
    ) -> None:
        """Bind the capability to one authority, response object, and body."""

        self.__pid = os.getpid()
        self.__transaction_key = transaction_key
        self.__authority_identity = authority_identity
        self.__response_identity = response_identity
        self.__status_code = status_code
        self.__body_sha256 = body_sha256

    def _matches(
        self,
        *,
        transaction_key: tuple[str, str],
        authority_identity: int,
        response_identity: int,
        status_code: int,
        body_sha256: str,
    ) -> bool:
        """Return whether every bound process-local identity still matches."""

        return bool(
            self.__pid == os.getpid()
            and self.__transaction_key == transaction_key
            and self.__authority_identity == authority_identity
            and self.__response_identity == response_identity
            and self.__status_code == status_code
            and self.__body_sha256 == body_sha256
        )

    def __reduce__(self) -> object:
        raise TypeError("consumed X responses are not serialisable")


class _BoundXRequestAuthority:
    """Opaque one-shot binding of a transaction to configured X transport."""

    __slots__ = (
        "__pid",
        "__authority_identity",
        "__source_identity",
        "__url",
        "__auth_identity",
        "__timeout_identity",
        "__payload_sha256",
    )

    def __init__(
        self,
        *,
        authority_identity: int,
        source_identity: int,
        url: str,
        auth_identity: int,
        timeout_identity: int,
        payload_sha256: str,
    ) -> None:
        self.__pid = os.getpid()
        self.__authority_identity = authority_identity
        self.__source_identity = source_identity
        self.__url = url
        self.__auth_identity = auth_identity
        self.__timeout_identity = timeout_identity
        self.__payload_sha256 = payload_sha256

    def _matches(
        self,
        *,
        authority: object,
        source_binding: object,
        url: str,
        auth: object,
        timeout: object,
        payload_sha256: str,
    ) -> bool:
        return bool(
            self.__pid == os.getpid()
            and self.__authority_identity == id(authority)
            and self.__source_identity == id(source_binding)
            and self.__url == url
            and self.__auth_identity == id(auth)
            and self.__timeout_identity == id(timeout)
            and self.__payload_sha256 == payload_sha256
        )

    def __reduce__(self) -> object:
        raise TypeError("bound X request authorities are not serialisable")


@dataclass(frozen=True)
class _ConfiguredXRequestInstallRecord:
    """Strong process record for app-owned X transport configuration."""

    owner_module: ModuleType
    provider: Callable[[], tuple[str, object, object]]
    installed_create_url: str
    installed_auth: object
    installed_timeout: object
    current_create_url: str
    current_auth: object
    current_timeout: object
    reload_fingerprint: tuple[object, ...]
    pid: int


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
    source_binding_identity: int = 0


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
class ExternallyConfirmedTransportAdoption:
    """Result of one network-free attempting-to-confirmed transition."""

    disposition: str
    previous_classification: str
    confirmed: ConfirmedTransportDetails
    journal_before: JournalSnapshot
    fence_before: JournalSnapshot
    journal_after: JournalSnapshot
    fence_after: JournalSnapshot
    prepared_audit_basename: str
    prepared_audit_sha256: str
    evidence_archive_basename: str
    evidence_sha256: str


@dataclass(frozen=True)
class _ExternalConfirmationTransitionLayout:
    """One exact, restart-visible external-confirmation journal exchange."""

    phase: str
    staging_name: str
    attempting: JournalSnapshot
    confirmed: JournalSnapshot
    fence: JournalSnapshot
    replacement_data: bytes


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
        _issued_source_binding_objects[_source_binding_key(binding)] = binding
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


def _source_binding_matches_current_receipt(
    source_binding: object,
    *,
    receipt_path: Path,
    expected_receipt: Mapping[str, Any],
) -> bool:
    """Match the caller and current file to one exact retained source binding."""

    if (
        not isinstance(source_binding, SourceReceiptBinding)
        or type(expected_receipt) is not dict
        or Path(receipt_path).absolute()
        != Path(source_binding.receipt_path).absolute()
        or dict(expected_receipt) != source_binding.receipt_document
    ):
        return False
    try:
        current = _read_stable_regular(
            Path(receipt_path),
            maximum=JOURNAL_MAX_BYTES,
            expected_mode=JOURNAL_MODE,
        )
    except (OSError, TransportJournalError):
        return False
    return bool(
        current.data == source_binding.receipt_bytes
        and hashlib.sha256(current.data).hexdigest()
        == source_binding.receipt_sha256
        and (
            int(current.metadata.st_dev),
            int(current.metadata.st_ino),
            int(current.metadata.st_ctime_ns),
            int(current.metadata.st_size),
        )
        == (
            source_binding.receipt_device,
            source_binding.receipt_inode,
            source_binding.receipt_ctime_ns,
            source_binding.receipt_size,
        )
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
    directory_fd = _open_directory(path.parent)
    os.close(directory_fd)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink not in allowed_link_counts
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) & 0o022
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


def _unlink_exact_stable_file(
    directory_fd: int,
    path: Path,
    expected: _StableFile,
    *,
    maximum: int,
    label: str,
    pre_unlink_verifier: Callable[[], None] | None = None,
) -> None:
    """Remove only the still-open exact inode and prove its link retired.

    A pathname validation followed by ``unlink`` is not an atomic comparison:
    another process can replace the entry in that interval.  Keep the
    validated inode open across unlink and the directory fsync, then require
    that its link count fell by exactly one.  A raced replacement therefore
    raises while a surviving companion remains the fail-closed barrier.
    """

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise TransportJournalError("O_NOFOLLOW is required")
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_fd,
        )
    except OSError as exc:
        raise TransportJournalError(f"{label} cannot be opened for removal") from exc
    try:
        opened = os.fstat(descriptor)
        data = _read_all(descriptor, maximum)
        before_unlink = os.fstat(descriptor)
        try:
            path_metadata = os.stat(
                path.name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError as exc:
            raise TransportJournalError(
                f"{label} vanished before exact removal"
            ) from exc
        expected_identity = _metadata_identity(expected.metadata)
        if (
            _metadata_identity(opened) != expected_identity
            or _metadata_identity(before_unlink) != expected_identity
            or _metadata_identity(path_metadata) != expected_identity
            or data != expected.data
        ):
            raise TransportJournalError(f"{label} changed before exact removal")
        if pre_unlink_verifier is not None:
            pre_unlink_verifier()
        os.unlink(path.name, dir_fd=directory_fd)
        os.fsync(directory_fd)
        retired = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        retired_data = _read_all(descriptor, maximum)
        after_retired_read = os.fstat(descriptor)
        if (
            int(retired.st_dev) != int(expected.metadata.st_dev)
            or int(retired.st_ino) != int(expected.metadata.st_ino)
            or int(retired.st_nlink) != int(expected.metadata.st_nlink) - 1
            or stat.S_IFMT(retired.st_mode)
            != stat.S_IFMT(expected.metadata.st_mode)
            or stat.S_IMODE(retired.st_mode)
            != stat.S_IMODE(expected.metadata.st_mode)
            or int(retired.st_uid) != int(expected.metadata.st_uid)
            or int(retired.st_size) != int(expected.metadata.st_size)
            or int(retired.st_mtime_ns) != int(expected.metadata.st_mtime_ns)
            or retired_data != expected.data
            or _metadata_identity(after_retired_read)
            != _metadata_identity(retired)
        ):
            raise TransportJournalError(
                f"{label} pathname removal did not retire the unchanged "
                "validated inode"
            )
        try:
            os.stat(
                path.name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise TransportJournalError(
                f"{label} pathname reappeared during exact removal"
            )
    finally:
        os.close(descriptor)


def _stable_file_matching_snapshot(
    path: Path,
    snapshot: JournalSnapshot,
    *,
    label: str,
) -> _StableFile:
    current = _read_stable_regular(
        path,
        maximum=JOURNAL_MAX_BYTES,
        expected_mode=JOURNAL_MODE,
    )
    if (
        current.data != snapshot.data
        or int(current.metadata.st_dev) != snapshot.device
        or int(current.metadata.st_ino) != snapshot.inode
        or int(current.metadata.st_ctime_ns) != snapshot.ctime_ns
    ):
        raise TransportJournalError(f"{label} changed before removal")
    return current


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    written = 0
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count <= 0:
            raise OSError("short write while publishing transport journal")
        written += count


def _open_directory(path: Path) -> int:
    """Open an owned, non-writable-by-others directory without following links."""
    from mrs_bot_state_generation import directory_identity

    try:
        before = directory_identity(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_uid, opened.st_mode) != before:
            os.close(descriptor)
            raise ValueError("directory identity changed")
        return descriptor
    except (OSError, ValueError) as exc:
        raise TransportJournalError("unsafe durable transaction directory") from exc


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
    pre_exchange_verifier: Callable[[], None] | None = None,
    pre_cleanup_verifier: Callable[[], None] | None = None,
    pre_unlink_verifier: Callable[[], None] | None = None,
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
        if pre_exchange_verifier is not None:
            pre_exchange_verifier()
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
        if pre_cleanup_verifier is not None:
            pre_cleanup_verifier()
        _unlink_exact_stable_file(
            directory_fd,
            staging,
            displaced,
            maximum=JOURNAL_MAX_BYTES,
            label="displaced transport journal",
            pre_unlink_verifier=pre_unlink_verifier,
        )
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
                cleanup_authorised = True
                if pre_cleanup_verifier is not None:
                    try:
                        pre_cleanup_verifier()
                    except BaseException:
                        cleanup_authorised = False
                if cleanup_authorised:
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
        or expected_device > MAX_FILESYSTEM_IDENTITY_INTEGER
        or type(expected_inode) is not int
        or expected_inode <= 0
        or expected_inode > MAX_FILESYSTEM_IDENTITY_INTEGER
        or type(expected_ctime_ns) is not int
        or expected_ctime_ns < 0
        or expected_ctime_ns > MAX_FILESYSTEM_TIMESTAMP_NS
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


def _validate_external_confirmation_identity(
    value: object,
    *,
    label: str,
) -> None:
    """Validate one exact pre-adoption filesystem identity record."""

    if (
        not isinstance(value, dict)
        or set(value) != {"sha256", "device", "inode", "ctime_ns", "size"}
        or type(value.get("sha256")) is not str
        or not _SHA256_RE.fullmatch(value["sha256"])
        or type(value.get("device")) is not int
        or not 0 <= value["device"] <= MAX_FILESYSTEM_IDENTITY_INTEGER
        or type(value.get("inode")) is not int
        or not 1 <= value["inode"] <= MAX_FILESYSTEM_IDENTITY_INTEGER
        or type(value.get("ctime_ns")) is not int
        or not 0 <= value["ctime_ns"] <= MAX_FILESYSTEM_TIMESTAMP_NS
        or type(value.get("size")) is not int
        or not 1 <= value["size"] <= JOURNAL_MAX_BYTES
    ):
        raise TransportJournalError(
            f"external confirmation {label} identity is invalid"
        )


def _validate_external_confirmation_binding(value: object) -> None:
    """Validate durable provenance carried only by an offline confirmation."""

    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema_version",
            "document_kind",
            "prepared_audit_basename",
            "prepared_audit_sha256",
            "evidence_archive_basename",
            "evidence_sha256",
            "attempting_journal",
            "prepared_fence",
        }
        or type(value.get("schema_version")) is not int
        or value.get("schema_version")
        != EXTERNAL_CONFIRMATION_BINDING_SCHEMA_VERSION
        or value.get("document_kind") != EXTERNAL_CONFIRMATION_BINDING_KIND
        or type(value.get("prepared_audit_basename")) is not str
        or not _AUDIT_BASENAME_RE.fullmatch(value["prepared_audit_basename"])
        or type(value.get("prepared_audit_sha256")) is not str
        or not _SHA256_RE.fullmatch(value["prepared_audit_sha256"])
        or type(value.get("evidence_archive_basename")) is not str
        or not _AUDIT_BASENAME_RE.fullmatch(value["evidence_archive_basename"])
        or type(value.get("evidence_sha256")) is not str
        or not _SHA256_RE.fullmatch(value["evidence_sha256"])
    ):
        raise TransportJournalError(
            "external transport confirmation binding is invalid"
        )
    _validate_external_confirmation_identity(
        value.get("attempting_journal"),
        label="attempting journal",
    )
    _validate_external_confirmation_identity(
        value.get("prepared_fence"),
        label="prepared fence",
    )


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
    allowed_fields = required | {"external_confirmation"}
    if frozenset(value) not in {frozenset(required), frozenset(allowed_fields)}:
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
        or source["device"] > MAX_FILESYSTEM_IDENTITY_INTEGER
        or source["inode"] <= 0
        or source["inode"] > MAX_FILESYSTEM_IDENTITY_INTEGER
        or source["ctime_ns"] < 0
        or source["ctime_ns"] > MAX_FILESYSTEM_TIMESTAMP_NS
        or source["size"] <= 0
        or source["size"] > JOURNAL_MAX_BYTES
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
            or not MIN_CONFIRMATION_EPOCH
            <= confirmation_epoch
            <= MAX_CONFIRMATION_EPOCH
        ):
            raise TransportJournalError("confirmed journal has no valid post ID")
    elif remote_post_id is not None or confirmation_epoch is not None:
        raise TransportJournalError("unconfirmed journal contains confirmation data")
    external_confirmation = value.get("external_confirmation")
    if external_confirmation is not None:
        if value["lifecycle_state"] != "confirmed":
            raise TransportJournalError(
                "unconfirmed journal contains external confirmation provenance"
            )
        _validate_external_confirmation_binding(external_confirmation)


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
        if (
            binding_key not in _issued_source_bindings
            or _issued_source_binding_objects.get(binding_key)
            is not source_binding
        ):
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
        source_binding_identity=id(source_binding),
    )
    with _authority_lock:
        _issued_source_bindings.discard(binding_key)
        _issued_source_binding_objects.pop(binding_key, None)
        prepared_key = (
            authority.journal_path,
            authority.transaction_id,
            "prepared",
        )
        transaction_key = (authority.journal_path, authority.transaction_id)
        _issued_untransmitted_authorities.add(prepared_key)
        _issued_untransmitted_authority_objects[prepared_key] = authority
        _transaction_source_bindings[transaction_key] = source_binding
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
            or _issued_untransmitted_authority_objects.get(prepared_key)
            is not authority
            or transaction_key in _aborting_transactions
            or transaction_key in _transitioning_transactions
        ):
            raise TransportJournalError("prepared transport authority was not issued")
        _issued_untransmitted_authorities.discard(prepared_key)
        _issued_untransmitted_authority_objects.pop(prepared_key, None)
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
        source_binding_identity=authority.source_binding_identity,
    )
    with _authority_lock:
        issued_key = (
            authority.journal_path,
            authority.transaction_id,
            "attempting",
        )
        _issued_untransmitted_authorities.add(issued_key)
        _issued_untransmitted_authority_objects[issued_key] = updated_authority
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
        if (
            issued_key not in _issued_untransmitted_authorities
            or _issued_untransmitted_authority_objects.get(issued_key)
            is not authority
            or _transaction_source_bindings.get(key) is None
        ):
            raise TransportJournalError(
                "exact transport authority was not issued in this process"
            )
        _issued_untransmitted_authorities.discard(issued_key)
        _issued_untransmitted_authority_objects.pop(issued_key, None)
        _consumed_authorities.add(key)
        _consumed_authority_objects[key] = authority


def _discard_consumed_x_response(consumed_response: object) -> None:
    """Invalidate a coordinator-issued response capability."""

    if not isinstance(consumed_response, _ConsumedXResponse):
        return
    with _authority_lock:
        registered = _consumed_x_responses.get(id(consumed_response))
        if registered is not None and registered[0] is consumed_response:
            _consumed_x_responses.pop(id(consumed_response), None)


def _claim_consumed_x_response_for_reply_rejection(
    consumed_response: object,
    *,
    authority: object,
    response: object,
    status_code: int,
    body_sha256: str,
) -> SourceReceiptBinding | None:
    """Claim a response and return its exact strongly-held source binding."""

    if not isinstance(consumed_response, _ConsumedXResponse) or not isinstance(
        authority,
        TransportAuthority,
    ):
        return None
    transaction_key = (
        str(Path(authority.journal_path).absolute()),
        authority.transaction_id,
    )
    with _authority_lock:
        registered = _consumed_x_responses.get(id(consumed_response))
        if (
            registered is None
            or registered[0] is not consumed_response
            or registered[1] != transaction_key
            or registered[2] is not authority
            or registered[3] is not response
            or _transaction_source_bindings.get(transaction_key)
            is not registered[4]
            or registered[5] != status_code
            or registered[6] != body_sha256
            or not consumed_response._matches(
                transaction_key=transaction_key,
                authority_identity=id(authority),
                response_identity=id(response),
                status_code=status_code,
                body_sha256=body_sha256,
            )
            or transaction_key not in _consumed_authorities
            or _consumed_authority_objects.get(transaction_key)
            is not authority
        ):
            return None
        _consumed_x_responses.pop(id(consumed_response), None)
        source_binding = registered[4]
    return source_binding


def _bind_transport_authority_to_configured_x_request(
    authority: TransportAuthority,
    *,
    payload: Mapping[str, Any],
) -> _BoundXRequestAuthority:
    """Bind one exact unconsumed authority to its configured X destination."""

    with _authority_lock:
        install_record = _configured_x_request_install_record
        configuration = (
            (
                install_record.current_create_url,
                install_record.current_auth,
                install_record.current_timeout,
            )
            if _configured_x_request_install_record_is_valid(install_record)
            else None
        )
    if configuration is None:
        raise TransportJournalError(
            "configured X request provider is unavailable in this process"
        )
    url, auth, timeout = configuration
    url = _validated_configured_x_create_url(url)
    payload_hash = payload_sha256(payload)
    transaction_key = (
        str(Path(authority.journal_path).absolute()),
        authority.transaction_id,
    )
    issued_key = (
        authority.journal_path,
        authority.transaction_id,
        "attempting",
    )
    if (
        authority.lifecycle_state != "attempting"
        or auth is None
        or timeout is None
        or payload_hash != authority.payload_sha256
    ):
        raise TransportJournalError(
            "configured X request does not bind the exact transaction"
        )
    bound_request: _BoundXRequestAuthority | None = None
    try:
        with _authority_lock:
            source_binding = _transaction_source_bindings.get(transaction_key)
            if (
                issued_key not in _issued_untransmitted_authorities
                or _issued_untransmitted_authority_objects.get(issued_key)
                is not authority
                or source_binding is None
                or _configured_x_request_install_record is not install_record
                or transaction_key in _consumed_authorities
                or transaction_key in _bound_x_request_transactions
            ):
                raise TransportJournalError(
                    "configured X request authority is stale or already bound"
                )
            bound_request = _BoundXRequestAuthority(
                authority_identity=id(authority),
                source_identity=id(source_binding),
                url=url,
                auth_identity=id(auth),
                timeout_identity=id(timeout),
                payload_sha256=payload_hash,
            )
            _bound_x_requests[id(bound_request)] = (
                bound_request,
                authority,
                source_binding,
                url,
                auth,
                timeout,
                payload_hash,
            )
            _bound_x_request_transactions[transaction_key] = id(bound_request)
        return bound_request
    except BaseException:
        if bound_request is not None:
            with _authority_lock:
                registered = _bound_x_requests.get(id(bound_request))
                if registered is not None and registered[0] is bound_request:
                    _bound_x_requests.pop(id(bound_request), None)
                if (
                    _bound_x_request_transactions.get(transaction_key)
                    == id(bound_request)
                ):
                    _bound_x_request_transactions.pop(transaction_key, None)
        raise


def _validated_configured_x_create_url(value: object) -> str:
    """Return one exact create URL supplied by sealed application config."""

    if type(value) is not str:
        raise TransportJournalError(
            "configured X request provider returned an invalid endpoint"
        )
    try:
        parsed_url = urlsplit(value)
        parsed_port = parsed_url.port
    except ValueError as exc:
        raise TransportJournalError(
            "configured X request provider returned an invalid endpoint"
        ) from exc
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.hostname
        or (parsed_port is None and ":" in parsed_url.netloc.rsplit("]", 1)[-1])
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.path != "/2/tweets"
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise TransportJournalError(
            "configured X request provider returned an invalid endpoint"
        )
    return value


def _configured_x_request_reload_fingerprint_is_valid(
    fingerprint: object,
) -> bool:
    """Return whether one app reload fingerprint has the sealed schema."""

    if type(fingerprint) is not tuple or len(fingerprint) != 6:
        return False
    version, test_mode, create_url, total_timeout, connect_timeout, digest = (
        fingerprint
    )
    try:
        _validated_configured_x_create_url(create_url)
    except TransportJournalError:
        return False
    return bool(
        version == "sealed-x-request-provider-reload-v1"
        and type(test_mode) is bool
        and type(total_timeout) is float
        and math.isfinite(total_timeout)
        and 0 < total_timeout <= 60.0
        and type(connect_timeout) is float
        and math.isfinite(connect_timeout)
        and connect_timeout == min(10.0, total_timeout)
        and type(digest) is str
        and _SHA256_RE.fullmatch(digest) is not None
    )


def _configured_x_timeout_matches_reload_fingerprint(
    timeout: object,
    fingerprint: tuple[object, ...],
) -> bool:
    """Return whether an evaluated timeout matches its sealed configuration."""

    try:
        total_timeout = timeout.total
        connect_timeout = timeout.connect_timeout
    except BaseException:
        return False
    return bool(
        type(total_timeout) is float
        and type(connect_timeout) is float
        and total_timeout == fingerprint[3]
        and connect_timeout == fingerprint[4]
    )


def _configured_x_create_url_is_loopback(url: str) -> bool:
    """Return whether one already-validated X create URL is loopback-only."""

    host = (urlsplit(url).hostname or "").casefold()
    if (
        host in {"localhost", "localhost.localdomain"}
        or host.endswith(".localhost")
    ):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _configured_x_request_install_record_fields_are_valid(
    record: object,
) -> bool:
    """Return whether one record retains its exact sealed strong fields."""

    if type(record) is not _ConfiguredXRequestInstallRecord:
        return False
    try:
        installed_create_url = _validated_configured_x_create_url(
            record.installed_create_url
        )
        _validated_configured_x_create_url(record.current_create_url)
    except TransportJournalError:
        return False
    return bool(
        type(record.owner_module) is ModuleType
        and sys.modules.get(record.owner_module.__name__) is record.owner_module
        and callable(record.provider)
        and _configured_x_request_reload_fingerprint_is_valid(
            record.reload_fingerprint
        )
        and installed_create_url == record.reload_fingerprint[2]
        and record.installed_auth is not None
        and record.current_auth is not None
        and record.current_timeout is not None
        and _configured_x_timeout_matches_reload_fingerprint(
            record.installed_timeout,
            record.reload_fingerprint,
        )
    )


def _configured_x_request_install_record_is_valid(record: object) -> bool:
    """Return whether one registry record is exact and current-process owned."""

    return bool(
        _configured_x_request_install_record_fields_are_valid(record)
        and record.pid == os.getpid()
    )


def _configured_x_request_reload_record(
    owner_module: object,
) -> tuple[str, tuple[object, ...] | None]:
    """Return the sealed app fingerprint without exposing mutable authority."""

    with _authority_lock:
        record = _configured_x_request_install_record
        if record is None:
            return "unconfigured", None
        if (
            not _configured_x_request_install_record_is_valid(record)
            or record.owner_module is not owner_module
        ):
            return "invalid", None
        return "installed", tuple(record.reload_fingerprint)


def _configured_x_request_provider_identity(
) -> Callable[[], tuple[str, object, object]] | None:
    """Return the installed compatibility provider for identity diagnostics."""

    with _authority_lock:
        record = _configured_x_request_install_record
        if not _configured_x_request_install_record_is_valid(record):
            return None
        return record.provider


def _configured_x_request_is_sealed_test_loopback() -> bool:
    """Return whether sealed app authority permits the local-test bypass."""

    with _authority_lock:
        record = _configured_x_request_install_record
        if not _configured_x_request_install_record_is_valid(record):
            return False
        fingerprint = record.reload_fingerprint
        provider_url = record.current_create_url
    if fingerprint[1] is not True:
        return False
    try:
        configured_url = _validated_configured_x_create_url(fingerprint[2])
        provider_url = _validated_configured_x_create_url(provider_url)
    except BaseException:
        return False
    with _authority_lock:
        return bool(
            _configured_x_request_install_record is record
            and _configured_x_create_url_is_loopback(configured_url)
            and _configured_x_create_url_is_loopback(provider_url)
        )


def _install_configured_x_request_provider(
    provider: Callable[[], tuple[str, object, object]],
    *,
    owner_module: object = None,
    reload_fingerprint: object = None,
) -> None:
    """Install one owner-bound, evaluated X transport configuration."""

    global _configured_x_request_install_record
    with _authority_lock:
        if _configured_x_request_install_record is not None:
            raise TransportJournalError(
                "configured X request provider was already installed"
            )
    if not callable(provider) or not (
        _configured_x_request_reload_fingerprint_is_valid(
            reload_fingerprint
        )
    ):
        raise TransportJournalError("configured X request provider is invalid")
    if (
        type(owner_module) is not ModuleType
        or sys.modules.get(owner_module.__name__) is not owner_module
    ):
        raise TransportJournalError(
            "configured X request provider lacks its exact app owner"
        )
    try:
        configuration = provider()
    except BaseException as exc:
        raise TransportJournalError(
            "configured X request provider could not be evaluated"
        ) from exc
    if type(configuration) is not tuple or len(configuration) != 3:
        raise TransportJournalError(
            "configured X request provider returned invalid authority"
        )
    create_url, auth, timeout = configuration
    create_url = _validated_configured_x_create_url(create_url)
    if (
        create_url != reload_fingerprint[2]
        or auth is None
        or getattr(owner_module, "AUTH", None) is not auth
        or getattr(owner_module, "IMPORT_TIME_TEST_MODE", None)
        is not reload_fingerprint[1]
        or not _configured_x_timeout_matches_reload_fingerprint(
            timeout,
            reload_fingerprint,
        )
    ):
        raise TransportJournalError(
            "configured X request provider does not match sealed app config"
        )
    sealed_provider = (
        lambda _url=create_url, _auth=auth, _timeout=timeout: (
            _url,
            _auth,
            _timeout,
        )
    )
    with _authority_lock:
        if _configured_x_request_install_record is not None:
            raise TransportJournalError(
                "configured X request provider was already installed"
            )
        _configured_x_request_install_record = (
            _ConfiguredXRequestInstallRecord(
                owner_module=owner_module,
                provider=sealed_provider,
                installed_create_url=create_url,
                installed_auth=auth,
                installed_timeout=timeout,
                current_create_url=create_url,
                current_auth=auth,
                current_timeout=timeout,
                reload_fingerprint=reload_fingerprint,
                pid=os.getpid(),
            )
        )


def _reset_configured_x_request_provider_for_tests(
    *,
    create_url: str,
    auth: object,
    timeout: object,
) -> None:
    """Install an isolated test endpoint only with no live transaction state."""

    if not _TEST_MODE_AT_IMPORT or os.getenv("MRS_TEST_MODE") != "1":
        raise TransportJournalError(
            "configured X request test reset is unavailable outside test mode"
        )
    create_url = _validated_configured_x_create_url(create_url)
    configured_host = urlsplit(create_url).hostname or ""
    try:
        loopback_host = ipaddress.ip_address(configured_host).is_loopback
    except ValueError:
        loopback_host = configured_host.casefold() == "localhost"
    if not loopback_host:
        raise TransportJournalError(
            "configured X request test reset requires a loopback endpoint"
        )
    if auth is None or timeout is None:
        raise TransportJournalError(
            "configured X request test reset received invalid authority"
        )
    from x_api_error_semantics import (
        _reply_create_rejection_proof_registry_is_empty,
    )

    if not _reply_create_rejection_proof_registry_is_empty():
        raise TransportJournalError(
            "configured X request test reset found an active rejection proof"
        )
    sealed_provider = (
        lambda _url=create_url, _auth=auth, _timeout=timeout: (
            _url,
            _auth,
            _timeout,
        )
    )
    global _configured_x_request_install_record
    with _authority_lock:
        install_record = _configured_x_request_install_record
        if (
            not _configured_x_request_install_record_is_valid(install_record)
            or _issued_source_bindings
            or _issued_untransmitted_authorities
            or _consumed_authorities
            or _consumed_x_responses
            or _bound_x_requests
            or _bound_x_request_transactions
            or _transaction_source_bindings
            or _transitioning_transactions
            or _aborting_transactions
        ):
            raise TransportJournalError(
                "configured X request test reset found active transaction state"
            )
        _configured_x_request_install_record = (
            _ConfiguredXRequestInstallRecord(
                owner_module=install_record.owner_module,
                provider=sealed_provider,
                installed_create_url=install_record.installed_create_url,
                installed_auth=install_record.installed_auth,
                installed_timeout=install_record.installed_timeout,
                current_create_url=create_url,
                current_auth=auth,
                current_timeout=timeout,
                reload_fingerprint=install_record.reload_fingerprint,
                pid=os.getpid(),
            )
        )


def _rebind_loopback_test_x_request_record_after_fork() -> None:
    """Re-seal an inherited loopback-only test record to the child PID."""

    global _configured_x_request_install_record
    record = _configured_x_request_install_record
    if (
        not _configured_x_request_install_record_fields_are_valid(record)
        or record.reload_fingerprint[1] is not True
        or not _configured_x_create_url_is_loopback(
            record.installed_create_url
        )
        or not _configured_x_create_url_is_loopback(record.current_create_url)
    ):
        return
    _configured_x_request_install_record = _ConfiguredXRequestInstallRecord(
        owner_module=record.owner_module,
        provider=record.provider,
        installed_create_url=record.installed_create_url,
        installed_auth=record.installed_auth,
        installed_timeout=record.installed_timeout,
        current_create_url=record.current_create_url,
        current_auth=record.current_auth,
        current_timeout=record.current_timeout,
        reload_fingerprint=record.reload_fingerprint,
        pid=os.getpid(),
    )


if hasattr(os, "register_at_fork"):
    os.register_at_fork(
        after_in_child=_rebind_loopback_test_x_request_record_after_fork
    )


def perform_consumed_x_request(
    path: Path,
    authority: TransportAuthority,
    *,
    request_authority: _BoundXRequestAuthority,
    payload: Mapping[str, Any],
    expected_receipt_path: Path,
    request_kwargs: Mapping[str, Any],
) -> tuple[requests.Response, object | None, object | None]:
    """Consume authority and itself cross the sole X request boundary."""

    request_options = dict(request_kwargs)
    if (
        set(request_options) != {"json", "allow_redirects"}
        or type(request_options.get("json")) is not dict
        or request_options["json"] != dict(payload)
        or request_options.get("allow_redirects") is not False
    ):
        raise TransportJournalError(
            "coordinated X request does not bind the exact create route"
        )
    payload_hash = payload_sha256(payload)
    transaction_key = (str(Path(path).absolute()), authority.transaction_id)
    with _authority_lock:
        registered_request = _bound_x_requests.get(id(request_authority))
        source_binding = _transaction_source_bindings.get(transaction_key)
        if (
            not isinstance(request_authority, _BoundXRequestAuthority)
            or registered_request is None
            or registered_request[0] is not request_authority
            or registered_request[1] is not authority
            or registered_request[2] is not source_binding
            or registered_request[6] != payload_hash
            or not request_authority._matches(
                authority=authority,
                source_binding=source_binding,
                url=registered_request[3],
                auth=registered_request[4],
                timeout=registered_request[5],
                payload_sha256=payload_hash,
            )
            or _bound_x_request_transactions.get(transaction_key)
            != id(request_authority)
        ):
            raise TransportJournalError(
                "coordinated X request lacks its exact configured authority"
            )
        _bound_x_requests.pop(id(request_authority), None)
        _bound_x_request_transactions.pop(transaction_key, None)
        request_url = registered_request[3]
        request_auth = registered_request[4]
        request_timeout = registered_request[5]
    consume_transport_authority(
        path,
        authority,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
        expected_receipt_path=expected_receipt_path,
    )
    with _authority_lock:
        if (
            transaction_key not in _consumed_authorities
            or _consumed_authority_objects.get(transaction_key)
            is not authority
            or _transaction_source_bindings.get(transaction_key) is None
        ):
            raise TransportJournalError(
                "coordinated X request did not consume the exact authority"
            )
    response = requests.request(
        "POST",
        request_url,
        auth=request_auth,
        timeout=request_timeout,
        **request_options,
    )
    status_code = getattr(response, "status_code", None)

    def response_authority_error(message: str) -> TransportJournalError:
        error = TransportJournalError(message)
        error.status_code = status_code if type(status_code) is int else None
        return error

    if type(response) is not requests.Response:
        raise response_authority_error(
            "coordinated X transport returned an untrusted response object"
        )
    response_url = getattr(response, "url", None)
    if response_url is not None and (
        type(response_url) is not str
        or (response_url and response_url != request_url)
    ):
        raise response_authority_error(
            "coordinated X response does not bind the configured endpoint"
        )
    prepared_request = getattr(response, "request", None)
    if prepared_request is not None:
        if type(prepared_request) is not requests.PreparedRequest:
            raise response_authority_error(
                "coordinated X response has an untrusted prepared request"
            )
        prepared_body = prepared_request.body
        if type(prepared_body) is str:
            try:
                prepared_body_bytes = prepared_body.encode("utf-8", "strict")
            except UnicodeError as exc:
                raise response_authority_error(
                    "coordinated X prepared body is not strict UTF-8"
                ) from exc
        elif type(prepared_body) is bytes:
            prepared_body_bytes = prepared_body
        else:
            raise response_authority_error(
                "coordinated X prepared body is not exact JSON bytes"
            )
        try:
            prepared_payload = _parse_strict_object_bytes(
                prepared_body_bytes,
                label="coordinated X prepared body",
            )
        except TransportJournalError as exc:
            raise response_authority_error(
                "coordinated X prepared body is not exact JSON"
            ) from exc
        if (
            prepared_request.method != "POST"
            or prepared_request.url != request_url
            or prepared_payload != dict(payload)
        ):
            raise response_authority_error(
                "coordinated X response does not bind the exact request"
            )
    response_history = getattr(response, "history", None)
    if type(response_history) is not list or response_history:
        raise response_authority_error(
            "coordinated X response does not prove one redirect-free hop"
        )
    if type(status_code) is int and 200 <= status_code < 300:
        return response, None, None
    raw_body = getattr(response, "content", None)
    if (
        type(raw_body) is not bytes
        or type(status_code) is not int
    ):
        return response, None, None
    consumed_response: _ConsumedXResponse | None = None
    try:
        body_sha256 = hashlib.sha256(raw_body).hexdigest()
        consumed_response = _ConsumedXResponse(
            transaction_key=transaction_key,
            authority_identity=id(authority),
            response_identity=id(response),
            status_code=status_code,
            body_sha256=body_sha256,
        )
        with _authority_lock:
            if (
                transaction_key not in _consumed_authorities
                or _consumed_authority_objects.get(transaction_key)
                is not authority
                or _transaction_source_bindings.get(transaction_key) is None
            ):
                raise TransportJournalError(
                    "coordinated X request lost consumed authority"
                )
            _consumed_x_responses[id(consumed_response)] = (
                consumed_response,
                transaction_key,
                authority,
                response,
                _transaction_source_bindings[transaction_key],
                status_code,
                body_sha256,
            )
        from x_api_error_semantics import (
            _prove_reply_create_rejection_from_actual_response,
            invalidate_reply_create_rejection_proof,
        )

        validated_response, rejection_proof = (
            _prove_reply_create_rejection_from_actual_response(
                raw_body=raw_body,
                status_code=status_code,
                payload=payload,
                authority=authority,
                consumed_response=consumed_response,
                actual_response=response,
            )
        )
        try:
            return response, validated_response, rejection_proof
        except BaseException:
            invalidate_reply_create_rejection_proof(rejection_proof)
            raise
    except BaseException:
        _discard_consumed_x_response(consumed_response)
        raise
    finally:
        _discard_consumed_x_response(consumed_response)


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
    if (
        type(confirmation_epoch) is not int
        or not MIN_CONFIRMATION_EPOCH
        <= confirmation_epoch
        <= MAX_CONFIRMATION_EPOCH
    ):
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
        if (
            key not in _consumed_authorities
            or _consumed_authority_objects.get(key) is not authority
            or _transaction_source_bindings.get(key) is None
        ):
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
    with _authority_lock:
        _consumed_authorities.discard(key)
        _consumed_authority_objects.pop(key, None)
        _transaction_source_bindings.pop(key, None)
    return updated


def _expected_adoption_identity(
    *,
    sha256: str,
    device: int,
    inode: int,
    ctime_ns: int,
    size: int,
    label: str,
) -> dict[str, object]:
    """Validate and return one canonical externally reviewed identity."""

    value: dict[str, object] = {
        "sha256": sha256,
        "device": device,
        "inode": inode,
        "ctime_ns": ctime_ns,
        "size": size,
    }
    _validate_external_confirmation_identity(value, label=label)
    return value


def _snapshot_matches_adoption_identity(
    snapshot: JournalSnapshot,
    expected: Mapping[str, object],
) -> bool:
    """Return whether a strict snapshot is the exact reviewed generation."""

    return bool(
        snapshot.sha256 == expected.get("sha256")
        and snapshot.device == expected.get("device")
        and snapshot.inode == expected.get("inode")
        and snapshot.ctime_ns == expected.get("ctime_ns")
        and len(snapshot.data) == expected.get("size")
    )


def _snapshot_matches_displaced_adoption_identity(
    snapshot: JournalSnapshot,
    expected: Mapping[str, object],
) -> bool:
    """Match an exchanged old inode while deliberately ignoring its ctime."""

    return bool(
        snapshot.sha256 == expected.get("sha256")
        and snapshot.device == expected.get("device")
        and snapshot.inode == expected.get("inode")
        and len(snapshot.data) == expected.get("size")
    )


def _inspect_exact_external_confirmation_transition(
    *,
    path: Path,
    attempting_journal_identity: Mapping[str, object],
    prepared_fence_identity: Mapping[str, object],
    confirmed_post_id: str,
    confirmation_epoch: int,
    external_binding: Mapping[str, object],
) -> _ExternalConfirmationTransitionLayout:
    """Prove one exact torn external-confirmation journal exchange.

    The staging basename alone carries no authority.  Both possible layouts
    are accepted only when one side is the reviewed attempting generation and
    the other side is the byte-exact externally confirmed replacement derived
    from it.  The displaced old inode's ctime is ignored only after exchange;
    its bytes, device, inode and size must still match the review.
    """

    path = Path(path)
    state = inspect_transport_state(path)
    if (
        state.classification != "lifecycle_transition_in_progress"
        or state.errors
        or len(state.staging_names) != 1
        or state.retirement_guard_names
        or state.journal is None
        or state.fence is None
    ):
        raise TransportJournalError(
            "external confirmation has no exact resumable staging transition"
        )
    staging_name = state.staging_names[0]
    if not _JOURNAL_STAGING_NAME_RE.fullmatch(staging_name):
        raise TransportJournalError(
            "external confirmation staging basename is invalid"
        )
    try:
        staging = _snapshot(path.parent / staging_name)
    except (FileNotFoundError, TransportJournalError) as exc:
        raise TransportJournalError(
            "external confirmation staging generation is unsafe"
        ) from exc

    live_lifecycle = state.journal.document.get("lifecycle_state")
    staging_lifecycle = staging.document.get("lifecycle_state")
    if (live_lifecycle, staging_lifecycle) == ("attempting", "confirmed"):
        phase = "resumable_before_transport_exchange"
        attempting = state.journal
        confirmed = staging
        attempting_identity_matches = _snapshot_matches_adoption_identity(
            attempting,
            attempting_journal_identity,
        )
    elif (live_lifecycle, staging_lifecycle) == ("confirmed", "attempting"):
        phase = "resumable_after_transport_exchange"
        attempting = staging
        confirmed = state.journal
        attempting_identity_matches = (
            _snapshot_matches_displaced_adoption_identity(
                attempting,
                attempting_journal_identity,
            )
        )
    else:
        raise TransportJournalError(
            "external confirmation staging generations are not one attempting/confirmed exchange"
        )
    if not attempting_identity_matches:
        raise TransportJournalError(
            "external confirmation staging transition lacks the reviewed attempting generation"
        )
    if (
        state.fence.document.get("lifecycle_state") != "prepared"
        or not _snapshot_matches_adoption_identity(
            state.fence,
            prepared_fence_identity,
        )
        or not _documents_share_transaction_identity(
            attempting.document,
            state.fence.document,
        )
    ):
        raise TransportJournalError(
            "external confirmation staging transition lacks the reviewed prepared fence"
        )

    replacement = {
        **attempting.document,
        "lifecycle_state": "confirmed",
        "remote_post_id": str(confirmed_post_id),
        "confirmation_epoch": confirmation_epoch,
        "external_confirmation": dict(external_binding),
    }
    replacement_data = canonical_json_bytes(replacement)
    if (
        confirmed.data != replacement_data
        or confirmed.document != replacement
        or not _documents_share_transaction_identity(
            confirmed.document,
            state.fence.document,
        )
    ):
        raise TransportJournalError(
            "external confirmation staging replacement differs from the exact reviewed result"
        )
    return _ExternalConfirmationTransitionLayout(
        phase=phase,
        staging_name=staging_name,
        attempting=attempting,
        confirmed=confirmed,
        fence=state.fence,
        replacement_data=replacement_data,
    )


def _finish_externally_confirmed_journal_exchange(
    *,
    path: Path,
    layout: _ExternalConfirmationTransitionLayout,
    attempting_journal_identity: Mapping[str, object],
    prepared_fence_identity: Mapping[str, object],
    confirmed_post_id: str,
    confirmation_epoch: int,
    external_binding: Mapping[str, object],
    mutation_authority: TransactionMutationAuthority | None,
) -> TransportJournalState:
    """Complete or clean up one already staged exact external confirmation."""

    path = Path(path)

    def inspect_phase(expected_phase: str) -> _ExternalConfirmationTransitionLayout:
        current = _inspect_exact_external_confirmation_transition(
            path=path,
            attempting_journal_identity=attempting_journal_identity,
            prepared_fence_identity=prepared_fence_identity,
            confirmed_post_id=confirmed_post_id,
            confirmation_epoch=confirmation_epoch,
            external_binding=external_binding,
        )
        if (
            current.phase != expected_phase
            or current.staging_name != layout.staging_name
            or current.attempting.data != layout.attempting.data
            or current.confirmed.data != layout.confirmed.data
            or current.attempting.device != layout.attempting.device
            or current.attempting.inode != layout.attempting.inode
            or current.attempting.ctime_ns != layout.attempting.ctime_ns
            or current.confirmed.device != layout.confirmed.device
            or current.confirmed.inode != layout.confirmed.inode
            or current.confirmed.ctime_ns != layout.confirmed.ctime_ns
        ):
            raise TransportJournalError(
                "external confirmation staging transition changed during recovery"
            )
        return current

    directory_fd = _open_directory(path.parent)
    try:
        current = inspect_phase(layout.phase)
        if layout.phase == "resumable_before_transport_exchange":
            require_transaction_mutation_authority(
                mutation_authority,
                operation=(
                    "external transport confirmation outstanding exchange"
                ),
            )
            current = inspect_phase("resumable_before_transport_exchange")
            require_transaction_mutation_authority(
                mutation_authority,
                operation=(
                    "external transport confirmation resumed atomic exchange"
                ),
            )
            _rename_exchange(directory_fd, path.name, layout.staging_name)
            os.fsync(directory_fd)
            require_transaction_mutation_authority(
                mutation_authority,
                operation=(
                    "external transport confirmation post-exchange displaced cleanup"
                ),
            )
            current = _inspect_exact_external_confirmation_transition(
                path=path,
                attempting_journal_identity=attempting_journal_identity,
                prepared_fence_identity=prepared_fence_identity,
                confirmed_post_id=confirmed_post_id,
                confirmation_epoch=confirmation_epoch,
                external_binding=external_binding,
            )
            if (
                current.phase != "resumable_after_transport_exchange"
                or current.staging_name != layout.staging_name
                or current.confirmed.data != layout.confirmed.data
                or current.confirmed.device != layout.confirmed.device
                or current.confirmed.inode != layout.confirmed.inode
                or current.attempting.data != layout.attempting.data
                or current.attempting.device != layout.attempting.device
                or current.attempting.inode != layout.attempting.inode
            ):
                raise TransportJournalError(
                    "external confirmation exchange did not preserve the exact generations"
                )
        else:
            os.fsync(directory_fd)
            require_transaction_mutation_authority(
                mutation_authority,
                operation=(
                    "external transport confirmation existing displaced cleanup"
                ),
            )
            current = inspect_phase("resumable_after_transport_exchange")

        confirmed_generation = current.confirmed
        displaced_path = path.parent / current.staging_name
        displaced = _read_stable_regular(
            displaced_path,
            maximum=JOURNAL_MAX_BYTES,
            expected_mode=JOURNAL_MODE,
        )
        if (
            displaced.data != current.attempting.data
            or int(displaced.metadata.st_dev) != current.attempting.device
            or int(displaced.metadata.st_ino) != current.attempting.inode
            or int(displaced.metadata.st_ctime_ns) != current.attempting.ctime_ns
        ):
            raise TransportJournalError(
                "external confirmation displaced generation changed before cleanup"
            )
        _unlink_exact_stable_file(
            directory_fd,
            displaced_path,
            displaced,
            maximum=JOURNAL_MAX_BYTES,
            label="externally confirmed displaced transport journal",
            pre_unlink_verifier=lambda: require_transaction_mutation_authority(
                mutation_authority,
                operation=(
                    "external transport confirmation exact unlink displaced cleanup"
                ),
            ),
        )
    finally:
        os.close(directory_fd)

    final_state = inspect_transport_state(path)
    if (
        final_state.classification != "confirmed_pair"
        or final_state.errors
        or final_state.staging_names
        or final_state.retirement_guard_names
        or final_state.journal is None
        or final_state.fence is None
        or final_state.journal.data != layout.replacement_data
        or final_state.journal.device != confirmed_generation.device
        or final_state.journal.inode != confirmed_generation.inode
        or final_state.journal.ctime_ns != confirmed_generation.ctime_ns
        or final_state.fence.data != layout.fence.data
        or final_state.fence.device != layout.fence.device
        or final_state.fence.inode != layout.fence.inode
        or final_state.fence.ctime_ns != layout.fence.ctime_ns
    ):
        raise TransportJournalError(
            "external confirmation resumed exchange did not produce the exact confirmed pair"
        )
    return final_state


def adopt_externally_confirmed_transport_transaction(
    *,
    path: Path,
    receipt_path: Path,
    mutation_authority: TransactionMutationAuthority | None = None,
    expected_transaction_id: str,
    expected_lane: str,
    expected_source_receipt_bytes: bytes,
    expected_source_receipt_sha256: str,
    expected_source_receipt_device: int,
    expected_source_receipt_inode: int,
    expected_source_receipt_ctime_ns: int,
    expected_source_receipt_size: int,
    expected_source_validator_id: str,
    expected_payload_bytes: bytes,
    expected_payload_sha256: str,
    expected_reply_target_id: str,
    expected_journal_sha256: str,
    expected_journal_device: int,
    expected_journal_inode: int,
    expected_journal_ctime_ns: int,
    expected_journal_size: int,
    expected_fence_sha256: str,
    expected_fence_device: int,
    expected_fence_inode: int,
    expected_fence_ctime_ns: int,
    expected_fence_size: int,
    confirmed_post_id: str,
    confirmation_epoch: int,
    prepared_audit_basename: str,
    prepared_audit_sha256: str,
    evidence_archive_basename: str,
    evidence_sha256: str,
) -> ExternallyConfirmedTransportAdoption:
    """Adopt one operator-reviewed published tweet offline.

    This low-level transition deliberately has no X response or network input.
    It accepts only the fixed conversational-reply or quote/image source receipt
    and either the exact reviewed ``attempting_pair`` or an externally-provenanced
    ``confirmed_pair`` produced by an identical earlier call.  The optional
    provenance carried by the confirmed journal makes a crash after the journal
    transition safely distinguishable from an unrelated ordinary confirmation.
    """

    require_transaction_mutation_authority(
        mutation_authority,
        operation="external transport confirmation adoption inspection",
    )
    path = Path(path)
    receipt_path = Path(receipt_path)
    if path.name != JOURNAL_BASENAME or path.parent != receipt_path.parent:
        raise TransportJournalError(
            "external confirmation journal path is not the source sibling"
        )
    is_reply = (
        receipt_path.name == "confirmed_reply_receipt.json"
        and expected_lane == "conversational_reply"
    )
    is_quote_image = (
        receipt_path.name == "regular_post_receipt.json"
        and expected_lane == "quote_image"
    )
    if not (is_reply or is_quote_image) or (
        expected_source_validator_id != LANE_SOURCE_VALIDATOR_ID
    ):
        raise TransportJournalError(
            "external confirmation is limited to the conversational-reply or "
            "quote/image source"
        )
    if (
        type(expected_transaction_id) is not str
        or not _SHA256_RE.fullmatch(expected_transaction_id)
    ):
        raise TransportJournalError("external confirmation transaction ID is invalid")
    if (
        type(expected_source_receipt_bytes) is not bytes
        or not expected_source_receipt_bytes
        or len(expected_source_receipt_bytes) > JOURNAL_MAX_BYTES
        or type(expected_source_receipt_sha256) is not str
        or not _SHA256_RE.fullmatch(expected_source_receipt_sha256)
        or hashlib.sha256(expected_source_receipt_bytes).hexdigest()
        != expected_source_receipt_sha256
        or len(expected_source_receipt_bytes) != expected_source_receipt_size
    ):
        raise TransportJournalError(
            "external confirmation source receipt bytes are invalid"
        )
    source_identity = _expected_adoption_identity(
        sha256=expected_source_receipt_sha256,
        device=expected_source_receipt_device,
        inode=expected_source_receipt_inode,
        ctime_ns=expected_source_receipt_ctime_ns,
        size=expected_source_receipt_size,
        label="source receipt",
    )
    if (
        type(expected_payload_bytes) is not bytes
        or not expected_payload_bytes
        or len(expected_payload_bytes) > JOURNAL_MAX_BYTES
        or type(expected_payload_sha256) is not str
        or not _SHA256_RE.fullmatch(expected_payload_sha256)
        or hashlib.sha256(expected_payload_bytes).hexdigest()
        != expected_payload_sha256
    ):
        raise TransportJournalError(
            "external confirmation canonical payload bytes are invalid"
        )
    payload = _parse_canonical(
        expected_payload_bytes,
        label="external confirmation canonical payload",
    )
    try:
        frozen_payload = freeze_tweet_request(
            method="POST",
            request_path="/2/tweets",
            payload=payload,
        )
    except TransportJournalError as exc:
        raise TransportJournalError(
            "external confirmation payload is not a supported tweet request"
        ) from exc
    reply_target_matches = bool(
        is_reply
        and type(expected_reply_target_id) is str
        and _POST_ID_RE.fullmatch(expected_reply_target_id)
        and payload.get("reply")
        == {"in_reply_to_tweet_id": expected_reply_target_id}
    )
    root_post_matches = bool(
        is_quote_image
        and expected_reply_target_id == ""
        and "reply" not in payload
    )
    if (
        frozen_payload.payload_bytes != expected_payload_bytes
        or frozen_payload.payload_sha256 != expected_payload_sha256
        or not (reply_target_matches or root_post_matches)
    ):
        raise TransportJournalError(
            "external confirmation payload does not match the expected tweet kind"
        )
    if not _POST_ID_RE.fullmatch(str(confirmed_post_id or "")):
        raise TransportJournalError("external confirmation has no valid post ID")
    if (
        type(confirmation_epoch) is not int
        or not MIN_CONFIRMATION_EPOCH
        <= confirmation_epoch
        <= MAX_CONFIRMATION_EPOCH
    ):
        raise TransportJournalError("external confirmation epoch is invalid")

    attempting_journal_identity = _expected_adoption_identity(
        sha256=expected_journal_sha256,
        device=expected_journal_device,
        inode=expected_journal_inode,
        ctime_ns=expected_journal_ctime_ns,
        size=expected_journal_size,
        label="attempting journal",
    )
    prepared_fence_identity = _expected_adoption_identity(
        sha256=expected_fence_sha256,
        device=expected_fence_device,
        inode=expected_fence_inode,
        ctime_ns=expected_fence_ctime_ns,
        size=expected_fence_size,
        label="prepared fence",
    )
    external_binding: dict[str, object] = {
        "schema_version": EXTERNAL_CONFIRMATION_BINDING_SCHEMA_VERSION,
        "document_kind": EXTERNAL_CONFIRMATION_BINDING_KIND,
        "prepared_audit_basename": prepared_audit_basename,
        "prepared_audit_sha256": prepared_audit_sha256,
        "evidence_archive_basename": evidence_archive_basename,
        "evidence_sha256": evidence_sha256,
        "attempting_journal": attempting_journal_identity,
        "prepared_fence": prepared_fence_identity,
    }
    _validate_external_confirmation_binding(external_binding)

    state = inspect_transport_state(path)
    if (
        state.classification
        not in {
            "attempting_pair",
            "confirmed_pair",
            "lifecycle_transition_in_progress",
        }
        or state.errors
        or state.retirement_guard_names
        or state.journal is None
        or state.fence is None
        or (
            state.classification == "lifecycle_transition_in_progress"
            and len(state.staging_names) != 1
        )
        or (
            state.classification != "lifecycle_transition_in_progress"
            and state.staging_names
        )
    ):
        raise TransportJournalError(
            "external confirmation requires one exact attempting, adopted, or resumable pair"
        )
    torn_layout = (
        _inspect_exact_external_confirmation_transition(
            path=path,
            attempting_journal_identity=attempting_journal_identity,
            prepared_fence_identity=prepared_fence_identity,
            confirmed_post_id=str(confirmed_post_id),
            confirmation_epoch=confirmation_epoch,
            external_binding=external_binding,
        )
        if state.classification == "lifecycle_transition_in_progress"
        else None
    )
    journal_before = (
        torn_layout.attempting if torn_layout is not None else state.journal
    )
    fence_before = state.fence
    live_journal_document = state.journal.document
    journal_document = journal_before.document
    fence_document = fence_before.document
    if (
        fence_document.get("lifecycle_state") != "prepared"
        or not _snapshot_matches_adoption_identity(
            fence_before,
            prepared_fence_identity,
        )
        or not _documents_share_transaction_identity(
            journal_document,
            fence_document,
        )
    ):
        raise TransportJournalError(
            "external confirmation prepared fence differs from the reviewed pair"
        )
    documents_to_validate = [journal_document, fence_document]
    if torn_layout is not None:
        documents_to_validate.append(torn_layout.confirmed.document)
    for document in documents_to_validate:
        source = document.get("source_receipt")
        source_validation = document.get("source_validation")
        if (
            document.get("transaction_id") != expected_transaction_id
            or document.get("lane") != expected_lane
            or document.get("request_method") != "POST"
            or document.get("request_path") != "/2/tweets"
            or document.get("remote_payload") != payload
            or document.get("remote_payload_sha256")
            != expected_payload_sha256
            or not isinstance(source, dict)
            or source.get("basename") != receipt_path.name
            or source.get("sha256") != expected_source_receipt_sha256
            or source.get("device") != expected_source_receipt_device
            or source.get("inode") != expected_source_receipt_inode
            or source.get("ctime_ns") != expected_source_receipt_ctime_ns
            or source.get("size") != expected_source_receipt_size
            or not isinstance(source_validation, dict)
            or source_validation.get("validator_id")
            != expected_source_validator_id
            or source_validation.get("receipt_sha256")
            != expected_source_receipt_sha256
            or source_validation.get("payload_sha256")
            != expected_payload_sha256
        ):
            raise TransportJournalError(
                "external confirmation pair differs from the exact source binding"
            )

    try:
        receipt = _read_stable_regular(
            receipt_path,
            maximum=JOURNAL_MAX_BYTES,
            expected_mode=JOURNAL_MODE,
        )
    except FileNotFoundError as exc:
        raise TransportJournalError(
            "external confirmation source receipt is missing"
        ) from exc
    if (
        receipt.data != expected_source_receipt_bytes
        or hashlib.sha256(receipt.data).hexdigest()
        != expected_source_receipt_sha256
        or (
            int(receipt.metadata.st_dev),
            int(receipt.metadata.st_ino),
            int(receipt.metadata.st_ctime_ns),
            int(receipt.metadata.st_size),
        )
        != (
            source_identity["device"],
            source_identity["inode"],
            source_identity["ctime_ns"],
            source_identity["size"],
        )
    ):
        raise TransportJournalError(
            "external confirmation source receipt changed after review"
        )

    if state.classification == "confirmed_pair":
        if (
            live_journal_document.get("lifecycle_state") != "confirmed"
            or live_journal_document.get("remote_post_id")
            != str(confirmed_post_id)
            or live_journal_document.get("confirmation_epoch")
            != confirmation_epoch
            or live_journal_document.get("external_confirmation")
            != external_binding
        ):
            raise TransportJournalError(
                "confirmed pair does not match the external adoption identity"
            )
        journal_after = state.journal
        fence_after = fence_before
        disposition = "already_adopted"
    else:
        transaction_key = (str(path.absolute()), expected_transaction_id)
        with _authority_lock:
            if transaction_key in _transitioning_transactions:
                raise TransportJournalError(
                    "external confirmation transaction is already transitioning"
                )
            _transitioning_transactions.add(transaction_key)
        try:
            # No path or document work belongs between this verifier call and
            # the exact exchange.  The verifier therefore proves the held
            # stopped-daemon boundary immediately before the sole destructive
            # transport transition.
            require_transaction_mutation_authority(
                mutation_authority,
                operation="external transport confirmation journal transition",
            )
            if torn_layout is not None:
                adopted_state = _finish_externally_confirmed_journal_exchange(
                    path=path,
                    layout=torn_layout,
                    attempting_journal_identity=attempting_journal_identity,
                    prepared_fence_identity=prepared_fence_identity,
                    confirmed_post_id=str(confirmed_post_id),
                    confirmation_epoch=confirmation_epoch,
                    external_binding=external_binding,
                    mutation_authority=mutation_authority,
                )
                replacement_data = torn_layout.replacement_data
            else:
                if (
                    journal_document.get("lifecycle_state") != "attempting"
                    or not _snapshot_matches_adoption_identity(
                        journal_before,
                        attempting_journal_identity,
                    )
                ):
                    raise TransportJournalError(
                        "attempting journal differs from the reviewed generation"
                    )
                replacement = {
                    **journal_document,
                    "lifecycle_state": "confirmed",
                    "remote_post_id": str(confirmed_post_id),
                    "confirmation_epoch": confirmation_epoch,
                    "external_confirmation": external_binding,
                }
                replacement_data = canonical_json_bytes(replacement)
                _replace_exact(
                    path,
                    expected=journal_before.data,
                    replacement=replacement_data,
                    expected_device=journal_before.device,
                    expected_inode=journal_before.inode,
                    expected_ctime_ns=journal_before.ctime_ns,
                    pre_exchange_verifier=lambda: require_transaction_mutation_authority(
                        mutation_authority,
                        operation=(
                            "external transport confirmation atomic exchange"
                        ),
                    ),
                    pre_cleanup_verifier=lambda: require_transaction_mutation_authority(
                        mutation_authority,
                        operation=(
                            "external transport confirmation displaced cleanup"
                        ),
                    ),
                    pre_unlink_verifier=lambda: require_transaction_mutation_authority(
                        mutation_authority,
                        operation=(
                            "external transport confirmation exact unlink displaced cleanup"
                        ),
                    ),
                )
                adopted_state = inspect_transport_state(path)
        finally:
            with _authority_lock:
                _transitioning_transactions.discard(transaction_key)
        if (
            adopted_state.classification != "confirmed_pair"
            or adopted_state.errors
            or adopted_state.staging_names
            or adopted_state.retirement_guard_names
            or adopted_state.journal is None
            or adopted_state.fence is None
            or adopted_state.journal.data != replacement_data
            or adopted_state.fence.sha256 != fence_before.sha256
            or (
                adopted_state.fence.device,
                adopted_state.fence.inode,
                adopted_state.fence.ctime_ns,
            )
            != (
                fence_before.device,
                fence_before.inode,
                fence_before.ctime_ns,
            )
        ):
            raise TransportJournalError(
                "external confirmation transition did not produce the exact confirmed pair"
            )
        journal_after = adopted_state.journal
        fence_after = adopted_state.fence
        disposition = (
            torn_layout.phase
            if torn_layout is not None
            else "first_adoption"
        )

    confirmed = inspect_confirmed_transport_transaction(path)
    if (
        confirmed.transaction_id != expected_transaction_id
        or confirmed.lane != expected_lane
        or confirmed.post_id != str(confirmed_post_id)
        or confirmed.confirmation_epoch != confirmation_epoch
        or confirmed.source_receipt_basename != receipt_path.name
        or confirmed.source_receipt_sha256 != expected_source_receipt_sha256
        or confirmed.source_validator_id != expected_source_validator_id
        or confirmed.payload_bytes != expected_payload_bytes
        or confirmed.payload_sha256 != expected_payload_sha256
    ):
        raise TransportJournalError(
            "external confirmation result differs from the reviewed transaction"
        )
    return ExternallyConfirmedTransportAdoption(
        disposition=disposition,
        previous_classification=state.classification,
        confirmed=confirmed,
        journal_before=journal_before,
        fence_before=fence_before,
        journal_after=journal_after,
        fence_after=fence_after,
        prepared_audit_basename=prepared_audit_basename,
        prepared_audit_sha256=prepared_audit_sha256,
        evidence_archive_basename=evidence_archive_basename,
        evidence_sha256=evidence_sha256,
    )


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
        if _transaction_source_bindings.get(transaction_key) is not source_binding:
            raise TransportJournalError(
                "untransmitted transaction is not provably bound to this source"
            )
        if lifecycle == "attempting":
            if (
                authority is None
                or authority.lifecycle_state != "attempting"
                or authority.transaction_id != document["transaction_id"]
                or issued_key not in _issued_untransmitted_authorities
                or _issued_untransmitted_authority_objects.get(issued_key)
                is not authority
                or (str(path.absolute()), authority.transaction_id)
                in _consumed_authorities
            ):
                raise TransportJournalError(
                    "attempting transaction is not provably untransmitted"
                )
        elif lifecycle != "prepared":
            raise TransportJournalError("confirmed transaction cannot be aborted")
        _issued_untransmitted_authorities.discard(issued_key)
        _issued_untransmitted_authority_objects.pop(issued_key, None)
        _aborting_transactions.add(transaction_key)
    directory_fd: int | None = None
    try:
        directory_fd = _open_directory(path.parent)
        current_journal = _stable_file_matching_snapshot(
            path,
            state.journal,
            label="untransmitted transport journal",
        )
        _unlink_exact_stable_file(
            directory_fd,
            path,
            current_journal,
            maximum=JOURNAL_MAX_BYTES,
            label="untransmitted transport journal",
        )
        # The immutable fence remains the restart barrier until journal
        # removal is durable.
        fence_path = fence_path_for_journal(path)
        current_fence = _stable_file_matching_snapshot(
            fence_path,
            state.fence,
            label="untransmitted transport fence",
        )
        _unlink_exact_stable_file(
            directory_fd,
            fence_path,
            current_fence,
            maximum=JOURNAL_MAX_BYTES,
            label="untransmitted transport fence",
        )
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        with _authority_lock:
            _aborting_transactions.discard(transaction_key)
    with _authority_lock:
        _transaction_source_bindings.pop(transaction_key, None)


def retire_consumed_transport_transaction_after_proved_remote_non_success(
    *,
    source_binding: SourceReceiptBinding,
    authority: TransportAuthority,
    remote_non_success_proof: object,
    mutation_authority: TransactionMutationAuthority | None = None,
) -> None:
    """Retire one consumed reply attempt after a proved X rejection.

    This is deliberately distinct from aborting an untransmitted transaction:
    the request crossed the transport boundary and consumed its authority.  It
    can be retired only in that same process, while the exact attempting
    journal/fence/source generation remains intact, and with an opaque proof
    produced from a validated target-specific X error response.  Any mismatch
    or filesystem uncertainty leaves at least one durable barrier to the
    caller's fail-closed handling.
    """

    from x_api_error_semantics import (
        begin_reply_create_rejection_transport_retirement,
        complete_reply_create_rejection_transport_retirement,
        invalidate_reply_create_rejection_proof,
    )

    require_transaction_mutation_authority(
        mutation_authority,
        operation="proved remote non-success transport retirement",
    )
    if not isinstance(source_binding, SourceReceiptBinding) or not isinstance(
        authority,
        TransportAuthority,
    ):
        raise TransportJournalError(
            "proved remote non-success retirement lacks exact transaction authority"
        )
    path = journal_path_for_receipt(Path(source_binding.receipt_path))
    fence_path = fence_path_for_journal(path)
    transaction_key = (str(path.absolute()), authority.transaction_id)
    with _authority_lock:
        if (
            transaction_key not in _consumed_authorities
            or _consumed_authority_objects.get(transaction_key)
            is not authority
            or _transaction_source_bindings.get(transaction_key)
            is not source_binding
            or transaction_key in _transitioning_transactions
            or transaction_key in _aborting_transactions
        ):
            raise TransportJournalError(
                "proved remote non-success authority was not consumed by this process"
            )
        _transitioning_transactions.add(transaction_key)

    directory_fd: int | None = None
    proof_claimed = False
    files_retired = False
    retired = False
    try:
        payload = source_binding.request.payload()
        reply = payload.get("reply") if type(payload) is dict else None
        target_id = (
            reply.get("in_reply_to_tweet_id")
            if type(reply) is dict
            else None
        )
        if (
            authority.lifecycle_state != "attempting"
            or authority.lane != "conversational_reply"
            or source_binding.lane != "conversational_reply"
            or source_binding.request.method != "POST"
            or source_binding.request.request_path != "/2/tweets"
            or type(reply) is not dict
            or set(reply) != {"in_reply_to_tweet_id"}
            or type(target_id) is not str
            or _POST_ID_RE.fullmatch(target_id) is None
            or Path(authority.journal_path) != path.absolute()
            or Path(authority.fence_path) != fence_path.absolute()
            or Path(source_binding.receipt_path).name
            != authority.source_receipt_basename
            or authority.source_validator_id != source_binding.validator_id
            or authority.source_binding_identity != id(source_binding)
            or authority.payload_sha256
            != source_binding.request.payload_sha256
        ):
            raise TransportJournalError(
                "proved remote non-success transaction identity is stale"
            )
        if not begin_reply_create_rejection_transport_retirement(
            remote_non_success_proof,
            authority=authority,
            source_binding=source_binding,
            payload_sha256=source_binding.request.payload_sha256,
            target_id=target_id,
        ):
            raise TransportJournalError(
                "proved remote non-success transaction identity is stale"
            )
        proof_claimed = True

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
            raise TransportJournalError(
                "proved remote non-success transaction is not one intact pair"
            )
        document = state.journal.document
        fence_document = state.fence.document
        if (
            document["transaction_id"] != authority.transaction_id
            or document["lifecycle_state"] != "attempting"
            or document["lane"] != "conversational_reply"
            or document["request_method"] != "POST"
            or document["request_path"] != "/2/tweets"
            or document["remote_payload"] != payload
            or document["remote_payload_sha256"]
            != source_binding.request.payload_sha256
            or document["source_receipt"]["basename"]
            != Path(source_binding.receipt_path).name
            or document["source_receipt"]["sha256"]
            != source_binding.receipt_sha256
            or document["source_receipt"]["device"]
            != source_binding.receipt_device
            or document["source_receipt"]["inode"]
            != source_binding.receipt_inode
            or document["source_receipt"]["ctime_ns"]
            != source_binding.receipt_ctime_ns
            or document["source_receipt"]["size"]
            != source_binding.receipt_size
            or document["source_validation"]["validator_id"]
            != source_binding.validator_id
            or document["source_validation"]["receipt_sha256"]
            != source_binding.receipt_sha256
            or document["source_validation"]["payload_sha256"]
            != source_binding.request.payload_sha256
            or fence_document["remote_payload"] != payload
            or state.journal.sha256 != authority.journal_sha256
            or (
                state.journal.device,
                state.journal.inode,
                state.journal.ctime_ns,
            )
            != (
                authority.journal_device,
                authority.journal_inode,
                authority.journal_ctime_ns,
            )
            or state.fence.sha256 != authority.fence_sha256
            or (
                state.fence.device,
                state.fence.inode,
                state.fence.ctime_ns,
            )
            != (
                authority.fence_device,
                authority.fence_inode,
                authority.fence_ctime_ns,
            )
        ):
            raise TransportJournalError(
                "proved remote non-success transaction identity is stale"
            )
        current_receipt = _read_stable_regular(
            Path(source_binding.receipt_path),
            maximum=JOURNAL_MAX_BYTES,
        )
        if (
            current_receipt.data != source_binding.receipt_bytes
            or hashlib.sha256(current_receipt.data).hexdigest()
            != source_binding.receipt_sha256
            or (
                int(current_receipt.metadata.st_dev),
                int(current_receipt.metadata.st_ino),
                int(current_receipt.metadata.st_ctime_ns),
                int(current_receipt.metadata.st_size),
            )
            != (
                source_binding.receipt_device,
                source_binding.receipt_inode,
                source_binding.receipt_ctime_ns,
                source_binding.receipt_size,
            )
        ):
            raise TransportJournalError(
                "proved remote non-success source receipt changed"
            )

        directory_fd = _open_directory(path.parent)
        current_journal = _stable_file_matching_snapshot(
            path,
            state.journal,
            label="proved-non-success transport journal",
        )
        _unlink_exact_stable_file(
            directory_fd,
            path,
            current_journal,
            maximum=JOURNAL_MAX_BYTES,
            label="proved-non-success transport journal",
        )
        # The immutable fence remains the restart barrier until journal
        # removal is durable, and is then removed only as the same generation.
        current_fence = _stable_file_matching_snapshot(
            fence_path,
            state.fence,
            label="proved-non-success transport fence",
        )
        _unlink_exact_stable_file(
            directory_fd,
            fence_path,
            current_fence,
            maximum=JOURNAL_MAX_BYTES,
            label="proved-non-success transport fence",
        )
        files_retired = True
    finally:
        try:
            if directory_fd is not None:
                os.close(directory_fd)
            if proof_claimed and files_retired:
                if not complete_reply_create_rejection_transport_retirement(
                    remote_non_success_proof
                ):
                    raise TransportJournalError(
                        "proved remote non-success capability could not be advanced"
                    )
                retired = True
        finally:
            if proof_claimed and not retired:
                invalidate_reply_create_rejection_proof(
                    remote_non_success_proof
                )
            with _authority_lock:
                if retired:
                    _consumed_authorities.discard(transaction_key)
                    _consumed_authority_objects.pop(transaction_key, None)
                    _transaction_source_bindings.pop(transaction_key, None)
                _transitioning_transactions.discard(transaction_key)


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
                current_fence = _stable_file_matching_snapshot(
                    fence_path,
                    state.fence,
                    label="confirmed transport fence",
                )
                _unlink_exact_stable_file(
                    directory_fd,
                    fence_path,
                    current_fence,
                    maximum=JOURNAL_MAX_BYTES,
                    label="confirmed transport fence",
                    pre_unlink_verifier=lambda: require_transaction_mutation_authority(
                        mutation_authority, operation="confirmed retirement unlink",
                    ),
                )
            require_exact_prepared_source("journal")
            if state.journal is not None:
                current_journal = _stable_file_matching_snapshot(
                    path,
                    state.journal,
                    label="confirmed transport journal",
                )
                _unlink_exact_stable_file(
                    directory_fd,
                    path,
                    current_journal,
                    maximum=JOURNAL_MAX_BYTES,
                    label="confirmed transport journal",
                    pre_unlink_verifier=lambda: require_transaction_mutation_authority(
                        mutation_authority, operation="confirmed retirement unlink",
                    ),
                )
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

        def require_exact_guarded_receipt(
            stage: str,
        ) -> tuple[_StableFile, _StableFile]:
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
            return current, guard

        require_exact_guarded_receipt("journal")
        if state.journal is not None:
            current_journal = _stable_file_matching_snapshot(
                path,
                state.journal,
                label="confirmed transport journal",
            )
            _unlink_exact_stable_file(
                directory_fd,
                path,
                current_journal,
                maximum=JOURNAL_MAX_BYTES,
                label="confirmed transport journal",
                pre_unlink_verifier=lambda: require_transaction_mutation_authority(
                    mutation_authority, operation="confirmed retirement unlink",
                ),
            )
        require_exact_guarded_receipt("fence")
        if state.fence is not None:
            current_fence = _stable_file_matching_snapshot(
                fence_path,
                state.fence,
                label="confirmed transport fence",
            )
            _unlink_exact_stable_file(
                directory_fd,
                fence_path,
                current_fence,
                maximum=JOURNAL_MAX_BYTES,
                label="confirmed transport fence",
                pre_unlink_verifier=lambda: require_transaction_mutation_authority(
                    mutation_authority, operation="confirmed retirement unlink",
                ),
            )
        _current_receipt, current_guard = require_exact_guarded_receipt("guard")
        _unlink_exact_stable_file(
            directory_fd,
            guard_path,
            current_guard,
            maximum=JOURNAL_MAX_BYTES,
            label="confirmed transport retirement guard",
            pre_unlink_verifier=lambda: require_transaction_mutation_authority(
                mutation_authority, operation="confirmed retirement unlink",
            ),
        )
    finally:
        os.close(directory_fd)


def reset_consumed_authorities_for_tests() -> None:
    """Clear process-only replay state for isolated tests."""

    with _authority_lock:
        _consumed_authorities.clear()
        _consumed_authority_objects.clear()
        _consumed_x_responses.clear()
        _bound_x_requests.clear()
        _bound_x_request_transactions.clear()
        _issued_untransmitted_authorities.clear()
        _issued_untransmitted_authority_objects.clear()
        _issued_source_bindings.clear()
        _issued_source_binding_objects.clear()
        _transaction_source_bindings.clear()
        _transitioning_transactions.clear()
        _aborting_transactions.clear()
    from x_api_error_semantics import (
        reset_reply_create_rejection_proofs_for_tests,
    )

    reset_reply_create_rejection_proofs_for_tests()

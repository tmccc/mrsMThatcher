#!/usr/bin/env python3
"""Validate and create the restart-persistent remote-write protocol sentinel.

The activation pathname is usable only with its immutable companion audit.
The audit is durably published first by the stopped activator and is bound to
an external operator attestation. Runtime inspection validates both files,
their relationship and their final pathname identities; a bare or torn pair
never opens remote-write lanes. Local namespace absence is never treated as
proof of a genuinely new installation.

Version 2 deliberately uses a new pathname and byte identity.  Runtime
inspection also requires the v1 namespace to be absent, so rolling the code
back to a v1-aware runtime cannot silently authorise a v2 deployment.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import math
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from exact_receipt_retirement import retirement_ledger_contract_sha256


PROTOCOL_VERSION = 2
ACTIVATION_BASENAME = ".mrs_remote_write_safety_protocol_v2"
ACTIVATION_BYTES = (
    b"mrsMThatcher remote-write safety protocol\n"
    b"version=transport-authority-restart-barrier-v2\n"
)
ACTIVATION_MODE = 0o400
ACTIVATION_AUDIT_BASENAME = f"{ACTIVATION_BASENAME}.activation_audit.json"
ACTIVATION_AUDIT_MODE = 0o400
ACTIVATION_AUDIT_SCHEMA_VERSION = 3
ACTIVATION_AUDIT_DOCUMENT_KIND = (
    "mrsMThatcher_remote_write_safety_protocol_v2_ledger_activation_audit"
)
PRE_LEDGER_ACTIVATION_AUDIT_SCHEMA_VERSION = 2
PRE_LEDGER_ACTIVATION_AUDIT_DOCUMENT_KIND = (
    "mrsMThatcher_remote_write_safety_protocol_v2_activation_audit"
)
LEGACY_PROTOCOL_VERSION = 1
LEGACY_ACTIVATION_BASENAME = ".mrs_remote_write_safety_protocol_v1"
LEGACY_ACTIVATION_BYTES = (
    b"mrsMThatcher remote-write safety protocol\n"
    b"version=successor-first-restart-barrier-v1\n"
)
LEGACY_ACTIVATION_AUDIT_BASENAME = (
    f"{LEGACY_ACTIVATION_BASENAME}.activation_audit.json"
)
LEGACY_ACTIVATION_AUDIT_SCHEMA_VERSION = 1
LEGACY_ACTIVATION_AUDIT_DOCUMENT_KIND = (
    "mrsMThatcher_remote_write_safety_protocol_activation_audit"
)
ESTABLISHED_INSTALL_ACTIVATION_KIND = (
    "established_install_external_clean_state_attestation"
)
RETIREMENT_LEDGER_RECEIPT_BASENAMES = (
    "regular_post_receipt.json",
    "meme_post_receipt.json",
    "confirmed_reply_receipt.json",
    "historical_context_reply_receipt.json",
)
_ACTIVATION_STAGING_PREFIX = f"{ACTIVATION_BASENAME}.pending."
_AUDIT_STAGING_PREFIX = f"{ACTIVATION_AUDIT_BASENAME}.pending."
_RENAME_NOREPLACE = 1
_MAX_AUDIT_BYTES = 16 * 1024
_MAX_JSON_STRUCTURE_DEPTH = 64
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class ProtocolActivationError(RuntimeError):
    """The protocol activation sentinel or companion audit is unsafe."""


@dataclass(frozen=True)
class ProtocolActivationSnapshot:
    """Bind one exact activation pair to stable filesystem identities."""

    device: int
    inode: int
    mode: int
    size: int
    sha256: str
    activation_kind: str
    audit_device: int
    audit_inode: int
    audit_mode: int
    audit_size: int
    audit_sha256: str


@dataclass(frozen=True)
class _StableFile:
    """One no-follow regular file read from a bound directory descriptor."""

    metadata: os.stat_result
    data: bytes


def _stable_metadata_identity(value: os.stat_result) -> tuple[int, ...]:
    """Return mutation-relevant metadata without access-time side effects."""

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


def _canonical_json_bytes(value: object) -> bytes:
    """Return the one accepted deterministic JSON representation."""

    return (
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON object names rather than accepting the last."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolActivationError(
                f"protocol activation audit contains duplicate key: {key}"
            )
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    """Reject non-finite JSON constants."""

    raise ProtocolActivationError(
        f"protocol activation audit contains invalid constant: {value}"
    )


def _parse_finite_json_float(value: str) -> float:
    """Return one finite JSON float or reject representation overflow."""

    parsed = float(value)
    if not math.isfinite(parsed):
        raise ProtocolActivationError(
            f"protocol activation audit contains non-finite number: {value}"
        )
    return parsed


def _require_bounded_json_structure(value: object) -> None:
    """Reject deeply nested control input before canonical re-encoding."""

    pending: list[tuple[object, int]] = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > _MAX_JSON_STRUCTURE_DEPTH:
            raise ProtocolActivationError(
                "protocol activation audit JSON nesting exceeds the limit"
            )
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)


def _read_exact(descriptor: int, maximum: int) -> bytes:
    """Read at most ``maximum`` bytes plus one overflow byte."""

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(4096, maximum + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum:
            raise ProtocolActivationError(
                "remote-write protocol activation file is oversized"
            )


def _inspect_stable_regular_at(
    directory_fd: int,
    basename: str,
    *,
    expected_mode: int,
    maximum_size: int,
    label: str,
    fsync_file: bool = False,
) -> _StableFile:
    """Read one single-link owned ordinary file without following links."""

    try:
        before = os.stat(basename, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ProtocolActivationError(f"{label} is missing") from exc
    except OSError as exc:
        raise ProtocolActivationError(f"{label} cannot be inspected") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) != expected_mode
        or before.st_size <= 0
        or before.st_size > maximum_size
    ):
        raise ProtocolActivationError(f"{label} has unsafe metadata")

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise ProtocolActivationError(
            f"O_NOFOLLOW is required for {label} inspection"
        )
    descriptor = os.open(
        basename,
        os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
        dir_fd=directory_fd,
    )
    try:
        opened = os.fstat(descriptor)
        data = _read_exact(descriptor, maximum_size)
        if fsync_file:
            os.fsync(descriptor)
        after_read = os.fstat(descriptor)
        after_path = os.stat(
            basename,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_uid",
            "st_size",
            "st_ctime_ns",
            "st_mtime_ns",
        )
        if (
            any(getattr(before, field) != getattr(opened, field) for field in stable_fields)
            or any(
                getattr(opened, field) != getattr(after_read, field)
                for field in stable_fields
            )
            or any(
                getattr(opened, field) != getattr(after_path, field)
                for field in stable_fields
            )
            or len(data) != opened.st_size
        ):
            raise ProtocolActivationError(f"{label} changed while inspected")
    except FileNotFoundError as exc:
        raise ProtocolActivationError(f"{label} changed while inspected") from exc
    finally:
        os.close(descriptor)
    return _StableFile(metadata=opened, data=data)


def _inspect_activation_sentinel_at(
    directory_fd: int,
    *,
    fsync_file: bool = False,
) -> _StableFile:
    """Return the exact stable sentinel without inspecting its audit."""

    inspected = _inspect_stable_regular_at(
        directory_fd,
        ACTIVATION_BASENAME,
        expected_mode=ACTIVATION_MODE,
        maximum_size=len(ACTIVATION_BYTES),
        label="remote-write protocol activation sentinel",
        fsync_file=fsync_file,
    )
    if inspected.data != ACTIVATION_BYTES:
        raise ProtocolActivationError(
            "remote-write protocol activation sentinel has invalid bytes"
        )
    return inspected


def _inspect_legacy_activation_sentinel_at(directory_fd: int) -> _StableFile:
    """Return the exact v1 sentinel solely for stopped migration validation."""

    inspected = _inspect_stable_regular_at(
        directory_fd,
        LEGACY_ACTIVATION_BASENAME,
        expected_mode=ACTIVATION_MODE,
        maximum_size=len(LEGACY_ACTIVATION_BYTES),
        label="legacy remote-write protocol activation sentinel",
    )
    if inspected.data != LEGACY_ACTIVATION_BYTES:
        raise ProtocolActivationError(
            "legacy remote-write protocol activation sentinel has invalid bytes"
        )
    return inspected


def _parse_activation_audit_version(
    data: bytes,
    *,
    activation_bytes: bytes,
    schema_version: int,
    document_kind: str,
    protocol_version: int,
    retirement_ledger_aware: bool = False,
) -> dict[str, object]:
    """Parse one canonical companion audit for an exact protocol generation."""

    try:
        decoded = data.decode("utf-8")
        value = json.loads(
            decoded,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
            parse_float=_parse_finite_json_float,
        )
    except ProtocolActivationError:
        raise
    except RecursionError as exc:
        raise ProtocolActivationError(
            "protocol activation audit JSON nesting exceeds the limit"
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolActivationError(
            "protocol activation audit is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ProtocolActivationError("protocol activation audit must be an object")
    _require_bounded_json_structure(value)
    try:
        canonical = _canonical_json_bytes(value)
    except RecursionError as exc:
        raise ProtocolActivationError(
            "protocol activation audit JSON nesting exceeds the limit"
        ) from exc
    if canonical != data:
        raise ProtocolActivationError(
            "protocol activation audit is not canonical JSON"
        )
    common = {
        "activation_kind",
        "activation_mode",
        "activation_sha256",
        "activation_size",
        "document_kind",
        "project_device",
        "project_inode",
        "schema_version",
    }
    if protocol_version >= 2:
        common |= {
            "legacy_activation_basename",
            "legacy_activation_sha256",
            "legacy_namespace_required_absent",
            "protocol_version",
        }
    if retirement_ledger_aware:
        common |= {
            "retirement_ledger_contract_sha256",
            "retirement_ledger_initial_inventory_sha256",
        }
    kind = value.get("activation_kind")
    if kind == ESTABLISHED_INSTALL_ACTIVATION_KIND:
        required = common | {
            "activator_cli_sha256",
            "clean_state_attestation_sha256",
            "clean_state_attestation_size",
            "external_operator_attestation_used",
            "operator_clean_state_claim_locally_proven",
            "reconciliation_reference",
        }
        valid_specific = (
            isinstance(value.get("activator_cli_sha256"), str)
            and _SHA256_RE.fullmatch(str(value.get("activator_cli_sha256"))) is not None
            and isinstance(value.get("clean_state_attestation_sha256"), str)
            and _SHA256_RE.fullmatch(str(value.get("clean_state_attestation_sha256")))
            is not None
            and type(value.get("clean_state_attestation_size")) is int
            and int(value.get("clean_state_attestation_size", 0)) > 0
            and value.get("external_operator_attestation_used") is True
            and value.get("operator_clean_state_claim_locally_proven") is False
            and isinstance(value.get("reconciliation_reference"), str)
            and bool(str(value.get("reconciliation_reference")).strip())
            and len(str(value.get("reconciliation_reference"))) <= 512
            and not any(
                ord(character) < 0x20
                for character in str(value.get("reconciliation_reference"))
            )
            and (
                not retirement_ledger_aware
                or (
                    isinstance(
                        value.get("retirement_ledger_contract_sha256"), str
                    )
                    and _SHA256_RE.fullmatch(
                        str(value.get("retirement_ledger_contract_sha256"))
                    )
                    is not None
                    and isinstance(
                        value.get("retirement_ledger_initial_inventory_sha256"),
                        str,
                    )
                    and _SHA256_RE.fullmatch(
                        str(
                            value.get(
                                "retirement_ledger_initial_inventory_sha256"
                            )
                        )
                    )
                    is not None
                )
            )
        )
    else:
        raise ProtocolActivationError(
            "protocol activation audit has unsupported activation kind"
        )
    if set(value) != required:
        raise ProtocolActivationError(
            "protocol activation audit has unexpected or missing fields"
        )
    if (
        type(value.get("schema_version")) is not int
        or value.get("schema_version") != schema_version
        or value.get("document_kind") != document_kind
        or value.get("activation_sha256")
        != hashlib.sha256(activation_bytes).hexdigest()
        or type(value.get("activation_size")) is not int
        or value.get("activation_size") != len(activation_bytes)
        or value.get("activation_mode") != oct(ACTIVATION_MODE)
        or type(value.get("project_device")) is not int
        or type(value.get("project_inode")) is not int
        or int(value.get("project_device", -1)) < 0
        or int(value.get("project_inode", 0)) <= 0
        or not valid_specific
    ):
        raise ProtocolActivationError(
            "protocol activation audit has invalid relationship fields"
        )
    if protocol_version >= 2 and (
        type(value.get("protocol_version")) is not int
        or value.get("protocol_version") != protocol_version
        or value.get("legacy_activation_basename")
        != LEGACY_ACTIVATION_BASENAME
        or value.get("legacy_activation_sha256")
        != hashlib.sha256(LEGACY_ACTIVATION_BYTES).hexdigest()
        or value.get("legacy_namespace_required_absent") is not True
    ):
        raise ProtocolActivationError(
            "protocol activation audit has invalid rollback fields"
        )
    return value


def _parse_activation_audit(data: bytes) -> dict[str, object]:
    """Parse and validate one canonical current-protocol companion audit."""

    value = _parse_activation_audit_version(
        data,
        activation_bytes=ACTIVATION_BYTES,
        schema_version=ACTIVATION_AUDIT_SCHEMA_VERSION,
        document_kind=ACTIVATION_AUDIT_DOCUMENT_KIND,
        protocol_version=PROTOCOL_VERSION,
        retirement_ledger_aware=True,
    )
    expected_contract = retirement_ledger_contract_sha256(
        tuple(Path(name) for name in RETIREMENT_LEDGER_RECEIPT_BASENAMES)
    )
    if value["retirement_ledger_contract_sha256"] != expected_contract:
        raise ProtocolActivationError(
            "protocol activation audit has the wrong retirement-ledger contract"
        )
    return value


def _parse_pre_ledger_activation_audit(data: bytes) -> dict[str, object]:
    """Parse the exact pre-ledger v2 audit solely for stopped migration."""

    return _parse_activation_audit_version(
        data,
        activation_bytes=ACTIVATION_BYTES,
        schema_version=PRE_LEDGER_ACTIVATION_AUDIT_SCHEMA_VERSION,
        document_kind=PRE_LEDGER_ACTIVATION_AUDIT_DOCUMENT_KIND,
        protocol_version=PROTOCOL_VERSION,
    )


def _parse_legacy_activation_audit(data: bytes) -> dict[str, object]:
    """Parse the exact v1 audit solely for stopped migration validation."""

    return _parse_activation_audit_version(
        data,
        activation_bytes=LEGACY_ACTIVATION_BYTES,
        schema_version=LEGACY_ACTIVATION_AUDIT_SCHEMA_VERSION,
        document_kind=LEGACY_ACTIVATION_AUDIT_DOCUMENT_KIND,
        protocol_version=LEGACY_PROTOCOL_VERSION,
    )


def _revalidate_stable_path_at(
    directory_fd: int,
    basename: str,
    expected: _StableFile,
    *,
    label: str,
) -> None:
    """Require one previously inspected pathname to retain the same identity."""

    try:
        current = os.stat(
            basename,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError as exc:
        raise ProtocolActivationError(
            f"{label} changed while the activation pair was inspected"
        ) from exc
    except OSError as exc:
        raise ProtocolActivationError(
            f"{label} cannot be revalidated"
        ) from exc
    if _stable_metadata_identity(current) != _stable_metadata_identity(
        expected.metadata
    ):
        raise ProtocolActivationError(
            f"{label} changed while the activation pair was inspected"
        )


def _namespace_entry_exists_at(directory_fd: int, basename: str) -> bool:
    """Return whether one direct entry exists without following links."""

    try:
        os.stat(basename, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ProtocolActivationError(
            f"protocol activation namespace cannot be inspected: {basename}"
        ) from exc
    return True


def _require_legacy_activation_namespace_absent_at(directory_fd: int) -> None:
    """Prevent a pre-v2 runtime from remaining authorised after v2 activation."""

    present = [
        basename
        for basename in (
            LEGACY_ACTIVATION_BASENAME,
            LEGACY_ACTIVATION_AUDIT_BASENAME,
        )
        if _namespace_entry_exists_at(directory_fd, basename)
    ]
    if present:
        raise ProtocolActivationError(
            "legacy protocol activation namespace remains present: "
            + ", ".join(present)
        )


def _inspect_legacy_protocol_activation_at(
    directory_fd: int,
) -> ProtocolActivationSnapshot:
    """Validate one complete v1 pair before a stopped v1-to-v2 migration."""

    directory_identity = os.fstat(directory_fd)
    if not stat.S_ISDIR(directory_identity.st_mode):
        raise ProtocolActivationError("protocol activation parent is not a directory")
    sentinel = _inspect_legacy_activation_sentinel_at(directory_fd)
    audit = _inspect_stable_regular_at(
        directory_fd,
        LEGACY_ACTIVATION_AUDIT_BASENAME,
        expected_mode=ACTIVATION_AUDIT_MODE,
        maximum_size=_MAX_AUDIT_BYTES,
        label="legacy remote-write protocol activation audit",
    )
    audit_value = _parse_legacy_activation_audit(audit.data)
    _revalidate_stable_path_at(
        directory_fd,
        LEGACY_ACTIVATION_BASENAME,
        sentinel,
        label="legacy remote-write protocol activation sentinel",
    )
    _revalidate_stable_path_at(
        directory_fd,
        LEGACY_ACTIVATION_AUDIT_BASENAME,
        audit,
        label="legacy remote-write protocol activation audit",
    )
    if (
        int(audit_value["project_device"]) != int(directory_identity.st_dev)
        or int(audit_value["project_inode"]) != int(directory_identity.st_ino)
        or audit_value["activation_sha256"]
        != hashlib.sha256(sentinel.data).hexdigest()
        or audit_value["activation_size"] != len(sentinel.data)
        or audit_value["activation_mode"]
        != oct(stat.S_IMODE(sentinel.metadata.st_mode))
    ):
        raise ProtocolActivationError(
            "legacy protocol activation audit does not bind the activation or parent"
        )
    return ProtocolActivationSnapshot(
        device=int(sentinel.metadata.st_dev),
        inode=int(sentinel.metadata.st_ino),
        mode=ACTIVATION_MODE,
        size=len(sentinel.data),
        sha256=hashlib.sha256(sentinel.data).hexdigest(),
        activation_kind=str(audit_value["activation_kind"]),
        audit_device=int(audit.metadata.st_dev),
        audit_inode=int(audit.metadata.st_ino),
        audit_mode=ACTIVATION_AUDIT_MODE,
        audit_size=len(audit.data),
        audit_sha256=hashlib.sha256(audit.data).hexdigest(),
    )


def _inspect_pre_ledger_protocol_activation_at(
    directory_fd: int,
) -> ProtocolActivationSnapshot:
    """Validate one complete pre-ledger v2 pair for stopped migration."""

    directory_identity = os.fstat(directory_fd)
    if not stat.S_ISDIR(directory_identity.st_mode):
        raise ProtocolActivationError("protocol activation parent is not a directory")
    _require_legacy_activation_namespace_absent_at(directory_fd)
    sentinel = _inspect_activation_sentinel_at(directory_fd)
    audit = _inspect_stable_regular_at(
        directory_fd,
        ACTIVATION_AUDIT_BASENAME,
        expected_mode=ACTIVATION_AUDIT_MODE,
        maximum_size=_MAX_AUDIT_BYTES,
        label="pre-ledger remote-write protocol activation audit",
    )
    audit_value = _parse_pre_ledger_activation_audit(audit.data)
    _revalidate_stable_path_at(
        directory_fd,
        ACTIVATION_BASENAME,
        sentinel,
        label="pre-ledger remote-write protocol activation sentinel",
    )
    _revalidate_stable_path_at(
        directory_fd,
        ACTIVATION_AUDIT_BASENAME,
        audit,
        label="pre-ledger remote-write protocol activation audit",
    )
    if (
        int(audit_value["project_device"]) != int(directory_identity.st_dev)
        or int(audit_value["project_inode"]) != int(directory_identity.st_ino)
        or audit_value["activation_sha256"]
        != hashlib.sha256(sentinel.data).hexdigest()
        or audit_value["activation_size"] != len(sentinel.data)
        or audit_value["activation_mode"]
        != oct(stat.S_IMODE(sentinel.metadata.st_mode))
    ):
        raise ProtocolActivationError(
            "pre-ledger protocol activation audit does not bind the activation or parent"
        )
    return ProtocolActivationSnapshot(
        device=int(sentinel.metadata.st_dev),
        inode=int(sentinel.metadata.st_ino),
        mode=ACTIVATION_MODE,
        size=len(sentinel.data),
        sha256=hashlib.sha256(sentinel.data).hexdigest(),
        activation_kind=str(audit_value["activation_kind"]),
        audit_device=int(audit.metadata.st_dev),
        audit_inode=int(audit.metadata.st_ino),
        audit_mode=ACTIVATION_AUDIT_MODE,
        audit_size=len(audit.data),
        audit_sha256=hashlib.sha256(audit.data).hexdigest(),
    )


def _inspect_protocol_activation_at(
    directory_fd: int,
    *,
    fsync_files: bool = False,
) -> ProtocolActivationSnapshot:
    """Return one exact sentinel and its valid hash-bound companion audit."""

    directory_identity = os.fstat(directory_fd)
    if not stat.S_ISDIR(directory_identity.st_mode):
        raise ProtocolActivationError("protocol activation parent is not a directory")
    _require_legacy_activation_namespace_absent_at(directory_fd)
    sentinel = _inspect_activation_sentinel_at(
        directory_fd,
        fsync_file=fsync_files,
    )
    audit = _inspect_stable_regular_at(
        directory_fd,
        ACTIVATION_AUDIT_BASENAME,
        expected_mode=ACTIVATION_AUDIT_MODE,
        maximum_size=_MAX_AUDIT_BYTES,
        label="remote-write protocol activation audit",
        fsync_file=fsync_files,
    )
    audit_value = _parse_activation_audit(audit.data)
    expected_ledger_contract = retirement_ledger_contract_sha256(
        tuple(Path(name) for name in RETIREMENT_LEDGER_RECEIPT_BASENAMES)
    )
    # Neither independently stable read is sufficient on its own: a namespace
    # mutation between them could otherwise compose a sentinel and audit from
    # different generations.  Revalidate both path identities only after both
    # contents and their relationship have been inspected.
    _revalidate_stable_path_at(
        directory_fd,
        ACTIVATION_BASENAME,
        sentinel,
        label="remote-write protocol activation sentinel",
    )
    _revalidate_stable_path_at(
        directory_fd,
        ACTIVATION_AUDIT_BASENAME,
        audit,
        label="remote-write protocol activation audit",
    )
    if (
        int(audit_value["project_device"]) != int(directory_identity.st_dev)
        or int(audit_value["project_inode"]) != int(directory_identity.st_ino)
        or audit_value["activation_sha256"]
        != hashlib.sha256(sentinel.data).hexdigest()
        or audit_value["activation_size"] != len(sentinel.data)
        or audit_value["activation_mode"]
        != oct(stat.S_IMODE(sentinel.metadata.st_mode))
        or audit_value["retirement_ledger_contract_sha256"]
        != expected_ledger_contract
    ):
        raise ProtocolActivationError(
            "protocol activation audit does not bind the activation or parent"
        )
    return ProtocolActivationSnapshot(
        device=int(sentinel.metadata.st_dev),
        inode=int(sentinel.metadata.st_ino),
        mode=ACTIVATION_MODE,
        size=len(sentinel.data),
        sha256=hashlib.sha256(sentinel.data).hexdigest(),
        activation_kind=str(audit_value["activation_kind"]),
        audit_device=int(audit.metadata.st_dev),
        audit_inode=int(audit.metadata.st_ino),
        audit_mode=ACTIVATION_AUDIT_MODE,
        audit_size=len(audit.data),
        audit_sha256=hashlib.sha256(audit.data).hexdigest(),
    )


def inspect_protocol_activation(path: Path) -> ProtocolActivationSnapshot:
    """Return the exact stable activation pair or fail closed."""

    path = Path(path)
    if path.name != ACTIVATION_BASENAME:
        raise ProtocolActivationError(
            "protocol activation path must use the fixed sentinel basename"
        )
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    directory_fd = os.open(path.parent, flags)
    try:
        return _inspect_protocol_activation_at(directory_fd)
    finally:
        os.close(directory_fd)


def _rename_noreplace_at(
    directory_fd: int,
    source_basename: str,
    destination_basename: str,
) -> None:
    """Atomically publish a complete staged file without replacement."""

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise ProtocolActivationError(
            "atomic no-replace protocol activation is unavailable"
        )
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        directory_fd,
        os.fsencode(source_basename),
        directory_fd,
        os.fsencode(destination_basename),
        _RENAME_NOREPLACE,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(error_number, os.strerror(error_number))
    if error_number in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
        raise ProtocolActivationError(
            "atomic no-replace protocol activation is unavailable"
        )
    raise OSError(error_number, os.strerror(error_number))


def _entry_absent(directory_fd: int, basename: str) -> bool:
    """Return true only when the direct child has no namespace entry."""

    try:
        os.stat(basename, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    except OSError as exc:
        raise ProtocolActivationError(
            "protocol activation namespace cannot be inspected"
        ) from exc
    return False


def _publish_or_revalidate_exact_file_at(
    directory_fd: int,
    *,
    basename: str,
    data: bytes,
    mode: int,
    staging_prefix: str,
    maximum_size: int,
    label: str,
) -> _StableFile:
    """Durably publish or exactly revalidate one immutable companion file."""

    if not _entry_absent(directory_fd, basename):
        before = _inspect_stable_regular_at(
            directory_fd,
            basename,
            expected_mode=mode,
            maximum_size=maximum_size,
            label=label,
            fsync_file=True,
        )
        if before.data != data:
            raise ProtocolActivationError(f"{label} has unexpected existing bytes")
        os.fsync(directory_fd)
        after = _inspect_stable_regular_at(
            directory_fd,
            basename,
            expected_mode=mode,
            maximum_size=maximum_size,
            label=label,
        )
        if (
            _stable_metadata_identity(before.metadata)
            != _stable_metadata_identity(after.metadata)
            or after.data != data
        ):
            raise ProtocolActivationError(f"{label} changed while re-synchronised")
        return after

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise ProtocolActivationError(f"O_NOFOLLOW is required for {label} creation")
    temporary = f"{staging_prefix}{os.getpid()}.{secrets.token_hex(12)}"
    descriptor: int | None = None
    published = False
    created: os.stat_result | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | nofollow
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory_fd,
        )
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(f"short write while staging {label}")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        created = os.fstat(descriptor)
        if (
            not stat.S_ISREG(created.st_mode)
            or created.st_nlink != 1
            or created.st_uid != os.geteuid()
            or stat.S_IMODE(created.st_mode) != mode
            or created.st_size != len(data)
            or os.pread(descriptor, len(data) + 1, 0) != data
        ):
            raise ProtocolActivationError(f"staged {label} failed exact validation")
        os.close(descriptor)
        descriptor = None
        try:
            _rename_noreplace_at(directory_fd, temporary, basename)
            published = True
        except FileExistsError:
            pass
        os.fsync(directory_fd)
        final = _inspect_stable_regular_at(
            directory_fd,
            basename,
            expected_mode=mode,
            maximum_size=maximum_size,
            label=label,
            fsync_file=True,
        )
        os.fsync(directory_fd)
        repeated = _inspect_stable_regular_at(
            directory_fd,
            basename,
            expected_mode=mode,
            maximum_size=maximum_size,
            label=label,
        )
        if (
            _stable_metadata_identity(final.metadata)
            != _stable_metadata_identity(repeated.metadata)
            or repeated.data != data
        ):
            raise ProtocolActivationError(f"{label} changed after publication")
        if published and created is not None and (
            repeated.metadata.st_dev != created.st_dev
            or repeated.metadata.st_ino != created.st_ino
        ):
            raise ProtocolActivationError(f"published {label} differs from staging")
        return repeated
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def build_established_install_activation_audit_bytes(
    *,
    project_device: int,
    project_inode: int,
    clean_state_attestation_sha256: str,
    clean_state_attestation_size: int,
    activator_cli_sha256: str,
    reconciliation_reference: str,
    retirement_ledger_contract_sha256_value: str,
    retirement_ledger_initial_inventory_sha256: str,
) -> bytes:
    """Build the exact companion audit for stopped established activation."""

    value = {
        "activation_kind": ESTABLISHED_INSTALL_ACTIVATION_KIND,
        "activation_mode": oct(ACTIVATION_MODE),
        "activation_sha256": hashlib.sha256(ACTIVATION_BYTES).hexdigest(),
        "activation_size": len(ACTIVATION_BYTES),
        "activator_cli_sha256": str(activator_cli_sha256),
        "clean_state_attestation_sha256": str(clean_state_attestation_sha256),
        "clean_state_attestation_size": int(clean_state_attestation_size),
        "document_kind": ACTIVATION_AUDIT_DOCUMENT_KIND,
        "external_operator_attestation_used": True,
        "legacy_activation_basename": LEGACY_ACTIVATION_BASENAME,
        "legacy_activation_sha256": hashlib.sha256(
            LEGACY_ACTIVATION_BYTES
        ).hexdigest(),
        "legacy_namespace_required_absent": True,
        "operator_clean_state_claim_locally_proven": False,
        "project_device": int(project_device),
        "project_inode": int(project_inode),
        "protocol_version": PROTOCOL_VERSION,
        "reconciliation_reference": str(reconciliation_reference),
        "retirement_ledger_contract_sha256": str(
            retirement_ledger_contract_sha256_value
        ),
        "retirement_ledger_initial_inventory_sha256": str(
            retirement_ledger_initial_inventory_sha256
        ),
        "schema_version": ACTIVATION_AUDIT_SCHEMA_VERSION,
    }
    data = _canonical_json_bytes(value)
    _parse_activation_audit(data)
    return data


def build_pre_ledger_established_install_activation_audit_bytes(
    *,
    project_device: int,
    project_inode: int,
    clean_state_attestation_sha256: str,
    clean_state_attestation_size: int,
    activator_cli_sha256: str,
    reconciliation_reference: str,
) -> bytes:
    """Reproduce the exact pre-ledger v2 audit for migration tests."""

    value = {
        "activation_kind": ESTABLISHED_INSTALL_ACTIVATION_KIND,
        "activation_mode": oct(ACTIVATION_MODE),
        "activation_sha256": hashlib.sha256(ACTIVATION_BYTES).hexdigest(),
        "activation_size": len(ACTIVATION_BYTES),
        "activator_cli_sha256": str(activator_cli_sha256),
        "clean_state_attestation_sha256": str(clean_state_attestation_sha256),
        "clean_state_attestation_size": int(clean_state_attestation_size),
        "document_kind": PRE_LEDGER_ACTIVATION_AUDIT_DOCUMENT_KIND,
        "external_operator_attestation_used": True,
        "legacy_activation_basename": LEGACY_ACTIVATION_BASENAME,
        "legacy_activation_sha256": hashlib.sha256(
            LEGACY_ACTIVATION_BYTES
        ).hexdigest(),
        "legacy_namespace_required_absent": True,
        "operator_clean_state_claim_locally_proven": False,
        "project_device": int(project_device),
        "project_inode": int(project_inode),
        "protocol_version": PROTOCOL_VERSION,
        "reconciliation_reference": str(reconciliation_reference),
        "schema_version": PRE_LEDGER_ACTIVATION_AUDIT_SCHEMA_VERSION,
    }
    data = _canonical_json_bytes(value)
    _parse_pre_ledger_activation_audit(data)
    return data


def build_legacy_established_install_activation_audit_bytes(
    *,
    project_device: int,
    project_inode: int,
    clean_state_attestation_sha256: str,
    clean_state_attestation_size: int,
    activator_cli_sha256: str,
    reconciliation_reference: str,
) -> bytes:
    """Reproduce an exact v1 audit for migration fixtures and verification."""

    value = {
        "activation_kind": ESTABLISHED_INSTALL_ACTIVATION_KIND,
        "activation_mode": oct(ACTIVATION_MODE),
        "activation_sha256": hashlib.sha256(
            LEGACY_ACTIVATION_BYTES
        ).hexdigest(),
        "activation_size": len(LEGACY_ACTIVATION_BYTES),
        "activator_cli_sha256": str(activator_cli_sha256),
        "clean_state_attestation_sha256": str(clean_state_attestation_sha256),
        "clean_state_attestation_size": int(clean_state_attestation_size),
        "document_kind": LEGACY_ACTIVATION_AUDIT_DOCUMENT_KIND,
        "external_operator_attestation_used": True,
        "operator_clean_state_claim_locally_proven": False,
        "project_device": int(project_device),
        "project_inode": int(project_inode),
        "reconciliation_reference": str(reconciliation_reference),
        "schema_version": LEGACY_ACTIVATION_AUDIT_SCHEMA_VERSION,
    }
    data = _canonical_json_bytes(value)
    _parse_legacy_activation_audit(data)
    return data


def _create_or_revalidate_protocol_activation_at(
    directory_fd: int,
    *,
    activation_audit_bytes: bytes,
) -> ProtocolActivationSnapshot:
    """Publish the durable audit first, then the exact activation sentinel."""

    audit_value = _parse_activation_audit(bytes(activation_audit_bytes))
    directory_identity = os.fstat(directory_fd)
    if (
        int(audit_value["project_device"]) != int(directory_identity.st_dev)
        or int(audit_value["project_inode"]) != int(directory_identity.st_ino)
    ):
        raise ProtocolActivationError(
            "protocol activation audit does not bind the opened project directory"
        )
    _publish_or_revalidate_exact_file_at(
        directory_fd,
        basename=ACTIVATION_AUDIT_BASENAME,
        data=bytes(activation_audit_bytes),
        mode=ACTIVATION_AUDIT_MODE,
        staging_prefix=_AUDIT_STAGING_PREFIX,
        maximum_size=_MAX_AUDIT_BYTES,
        label="remote-write protocol activation audit",
    )
    # The directory sync completed before the permission sentinel exists.
    # A hard exit therefore leaves either no sentinel or a sentinel whose
    # required companion audit is already durable.
    _publish_or_revalidate_exact_file_at(
        directory_fd,
        basename=ACTIVATION_BASENAME,
        data=ACTIVATION_BYTES,
        mode=ACTIVATION_MODE,
        staging_prefix=_ACTIVATION_STAGING_PREFIX,
        maximum_size=len(ACTIVATION_BYTES),
        label="remote-write protocol activation sentinel",
    )
    final = _inspect_protocol_activation_at(directory_fd, fsync_files=True)
    os.fsync(directory_fd)
    repeated = _inspect_protocol_activation_at(directory_fd)
    if final != repeated:
        raise ProtocolActivationError(
            "protocol activation pair changed before successful return"
        )
    return repeated


def create_protocol_activation_noreplace(path: Path) -> ProtocolActivationSnapshot:
    """Refuse the obsolete unaudited new-install activation shortcut.

    Local namespace absence cannot prove that an installation is genuinely
    new rather than an established installation whose durable evidence was
    lost.  Every installation must therefore use the stopped, lock-bound
    external-attestation activator.
    """

    del path
    raise ProtocolActivationError(
        "unaudited new-install protocol activation is unsupported; use the "
        "stopped external-attestation activator"
    )

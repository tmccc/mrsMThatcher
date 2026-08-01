#!/usr/bin/env python3
"""Activate the restart-persistent remote-write protocol on a stopped install.

This command is intentionally clean-state only.  It acquires the same complete
instance-lock boundary as the offline ambiguity-marker reconciler, refuses any
active safety marker or unresolved transaction receipt, verifies an immutable
external clean-state/reconciliation attestation bound to this CLI and project,
durably publishes its hash-bound activation audit, and only then creates the
exact activation sentinel without replacement.  It never reconciles or
deletes incident evidence.  The external assertion is supplied by an operator;
its truth is explicitly not something the local checks can prove.

An exact existing sentinel is an idempotent crash-recovery case: while all
locks and clean-state checks remain satisfied, the command re-synchronises and
revalidates its bytes, metadata and identity before returning.  A malformed or
unsafe existing entry remains blocked and is never replaced.  The service
wrapper/supervisor must be stopped separately; local file and process locks do
not pretend to prove supervisor policy.  After activation, rollback to a
protocol-unaware runtime is prohibited unless a separate stopped validation
establishes the explicitly reviewed clean rollback state.

The v2 activator also performs the sole supported v1 migration.  It validates
the complete v1 pair, durably removes the v1 permission sentinel before its
audit, and only then publishes the v2 audit and sentinel.  Thus every crash
point after migration begins leaves both runtimes unable to write until a
complete v2 pair exists; a mixed v1/v2 namespace is never accepted.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import socket
import stat
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from remote_write_safety_protocol import (  # noqa: E402
    ACTIVATION_AUDIT_BASENAME,
    ACTIVATION_AUDIT_MODE,
    ACTIVATION_BASENAME,
    ACTIVATION_BYTES,
    ACTIVATION_MODE,
    LEGACY_ACTIVATION_AUDIT_BASENAME,
    LEGACY_ACTIVATION_BASENAME,
    PROTOCOL_VERSION,
    ProtocolActivationError,
    _inspect_stable_regular_at,
    _inspect_legacy_protocol_activation_at,
    _parse_legacy_activation_audit,
    build_established_install_activation_audit_bytes,
    _create_or_revalidate_protocol_activation_at,
)
from remote_media_upload_receipt import (  # noqa: E402
    RETIREMENT_GUARD_PREFIX as MEDIA_RETIREMENT_GUARD_PREFIX,
    TRANSITION_PREFIX as MEDIA_TRANSITION_PREFIX,
    fence_path_for_receipt as media_fence_path_for_receipt,
)
from remote_write_transport_journal import (  # noqa: E402
    FENCE_BASENAME as TRANSPORT_FENCE_BASENAME,
    JOURNAL_BASENAME as TRANSPORT_JOURNAL_BASENAME,
    JOURNAL_RETIREMENT_PREFIX,
    JOURNAL_STAGING_PREFIX,
)
from exact_receipt_retirement import retirement_auxiliary_paths  # noqa: E402
from tools.reconcile_remote_write_safety_marker import (  # noqa: E402
    LOCK_BASENAME,
    MARKER_BASENAME,
    RESTART_BARRIER_BASENAME,
    BotStillRunningError,
    MarkerReconciliationError,
    UnsafeReconciliationPathError,
    _descriptor_owns_exclusive_flock,
    _ofd_lock_record,
    _open_project_directory_without_symlinks,
    _open_verified_regular,
    _require_project_path_identity,
    _revalidate_locked_instance_lock,
    instance_lock_abstract_socket_name_for_identity,
)


RECEIPT_BASENAMES = (
    "regular_post_receipt.json",
    "meme_post_receipt.json",
    "confirmed_reply_receipt.json",
    "historical_context_reply_receipt.json",
)
MEDIA_UPLOAD_RECEIPT_BASENAME = "remote_media_upload_receipt.json"
MEDIA_UPLOAD_FENCE_BASENAME = media_fence_path_for_receipt(
    Path(MEDIA_UPLOAD_RECEIPT_BASENAME)
).name
RECEIPT_RETIREMENT_AUXILIARY_BASENAMES = tuple(
    auxiliary.name
    for receipt_basename in RECEIPT_BASENAMES
    for auxiliary in retirement_auxiliary_paths(Path(receipt_basename))
)
REFUSED_STATE_BASENAMES = (
    MARKER_BASENAME,
    RESTART_BARRIER_BASENAME,
    *RECEIPT_BASENAMES,
    TRANSPORT_JOURNAL_BASENAME,
    TRANSPORT_FENCE_BASENAME,
    MEDIA_UPLOAD_RECEIPT_BASENAME,
    MEDIA_UPLOAD_FENCE_BASENAME,
    *RECEIPT_RETIREMENT_AUXILIARY_BASENAMES,
)
REFUSED_STATE_PREFIXES = (
    JOURNAL_STAGING_PREFIX,
    JOURNAL_RETIREMENT_PREFIX,
    MEDIA_TRANSITION_PREFIX,
    MEDIA_RETIREMENT_GUARD_PREFIX,
)
ESTABLISHED_STATE_BASENAMES = (
    "bot_state.json",
    "lines_used.json",
    "images_used.json",
)
CLEAN_STATE_ATTESTATION_SCHEMA_VERSION = 2
CLEAN_STATE_ATTESTATION_DOCUMENT_KIND = (
    "mrsMThatcher_remote_write_safety_clean_state_reconciliation_attestation"
)
CLEAN_STATE_ATTESTATION_MODE = 0o400
MAX_CLEAN_STATE_ATTESTATION_BYTES = 16 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class ProtocolActivationRefused(MarkerReconciliationError):
    """The stopped installation is not clean enough for activation."""


@dataclass(frozen=True)
class ProtocolActivationResult:
    """Describe one exact clean-state offline protocol activation."""

    schema_version: int
    operation: str
    protocol_version: int
    project_root: str
    activation_path: str
    activation_sha256: str
    activation_size: int
    activation_mode: str
    activation_device: int
    activation_inode: int
    activation_reused_existing: bool
    activation_migrated_from_protocol_version: int | None
    activation_kind: str
    activation_audit_path: str
    activation_audit_sha256: str
    activation_audit_size: int
    expected_project_root: str
    expected_project_device: int
    expected_project_inode: int
    established_state_files: tuple[str, ...]
    bot_instance_lock_acquired: bool
    supervisor_stopped_precondition_declared: bool
    supervisor_stopped_locally_proved: bool
    clean_state_attestation_sha256: str
    clean_state_attestation_size: int
    clean_state_attestation_source_path: str
    clean_state_attestation_stable_nofollow_read: bool
    activator_cli_sha256: str
    external_operator_attestation_used: bool
    operator_clean_state_claim_locally_proven: bool
    activation_audit_durable_before_sentinel: bool
    active_markers_absent: bool
    unresolved_receipts_absent: bool
    refused_state_basenames: tuple[str, ...]
    refused_state_prefixes: tuple[str, ...]
    refused_state_inventory_sha256: str
    legacy_activation_namespace_absent: bool
    successful_return_requires_exact_sentinel: bool
    rollback_to_protocol_unaware_runtime_prohibited: bool

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible record."""

        value = asdict(self)
        value["established_state_files"] = list(self.established_state_files)
        value["refused_state_basenames"] = list(self.refused_state_basenames)
        value["refused_state_prefixes"] = list(self.refused_state_prefixes)
        return value


@dataclass(frozen=True)
class _CleanStateAttestation:
    """One exact externally authored claim and its stable file identity."""

    source_path: str
    data: bytes
    sha256: str
    reconciliation_reference: str


@dataclass(frozen=True)
class _ActivationNamespacePlan:
    """One fail-closed current/legacy activation namespace disposition."""

    migrate_legacy: bool
    resume_after_legacy_sentinel_removal: bool
    reuse_current: bool
    legacy_sentinel_identity: tuple[int, int] | None
    legacy_audit_identity: tuple[int, int] | None


def _canonical_json_bytes(value: object) -> bytes:
    """Return the only accepted deterministic attestation representation."""

    return (
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def _refused_state_inventory_sha256() -> str:
    """Bind the complete deterministic exact/prefix activation inventory."""

    return hashlib.sha256(
        _canonical_json_bytes(
            {
                "basenames": list(REFUSED_STATE_BASENAMES),
                "prefixes": list(REFUSED_STATE_PREFIXES),
            }
        )
    ).hexdigest()


def _strict_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """Reject duplicate names in external operator attestations."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolActivationRefused(
                f"clean-state attestation contains duplicate key: {key}"
            )
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    """Reject non-finite JSON constants."""

    raise ProtocolActivationRefused(
        f"clean-state attestation contains invalid constant: {value}"
    )


def _normalise_sha256(value: str, *, label: str) -> str:
    """Return one lowercase SHA-256 or reject it."""

    candidate = str(value).strip().lower()
    if _SHA256_RE.fullmatch(candidate) is None:
        raise ProtocolActivationRefused(
            f"{label} must be 64 lowercase hexadecimal digits"
        )
    return candidate


def _read_descriptor_bytes(descriptor: int, *, maximum: int, label: str) -> bytes:
    """Read a bounded ordinary file from its already verified descriptor."""

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(4096, maximum + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum:
            raise ProtocolActivationRefused(f"{label} is oversized")


def _stable_cli_sha256() -> str:
    """Hash the exact no-follow activator source executing this operation."""

    source = Path(os.path.abspath(__file__))
    parent, parent_fd = _open_project_directory_without_symlinks(source.parent)
    descriptor: int | None = None
    try:
        descriptor, before = _open_verified_regular(
            parent_fd,
            source.name,
            label="activator CLI source",
            flags=os.O_RDONLY,
            require_single_link=True,
        )
        data = _read_descriptor_bytes(
            descriptor,
            maximum=2 * 1024 * 1024,
            label="activator CLI source",
        )
        after_fd = os.fstat(descriptor)
        after_path = os.stat(source.name, dir_fd=parent_fd, follow_symlinks=False)
        _require_project_path_identity(parent, parent_fd, os.fstat(parent_fd))
        stable = (
            int(before.st_dev),
            int(before.st_ino),
            int(before.st_mode),
            int(before.st_nlink),
            int(before.st_size),
            int(before.st_ctime_ns),
            int(before.st_mtime_ns),
        )
        if stable != (
            int(after_fd.st_dev),
            int(after_fd.st_ino),
            int(after_fd.st_mode),
            int(after_fd.st_nlink),
            int(after_fd.st_size),
            int(after_fd.st_ctime_ns),
            int(after_fd.st_mtime_ns),
        ) or stable != (
            int(after_path.st_dev),
            int(after_path.st_ino),
            int(after_path.st_mode),
            int(after_path.st_nlink),
            int(after_path.st_size),
            int(after_path.st_ctime_ns),
            int(after_path.st_mtime_ns),
        ):
            raise ProtocolActivationRefused(
                "activator CLI source changed while it was hashed"
            )
        return hashlib.sha256(data).hexdigest()
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)


def build_clean_state_attestation_bytes(
    *,
    project_root: Path,
    project_device: int,
    project_inode: int,
    activator_cli_sha256: str,
    reconciliation_reference: str,
) -> bytes:
    """Build the exact external-attestation schema for operator tooling/tests.

    Building bytes does not prove the claims.  The activator only verifies the
    file's identity, schema, hash and bindings; the operator remains the source
    of the clean-state/reconciliation assertion.
    """

    value = {
        "active_markers_absent_after_external_review": True,
        "activator_cli_sha256": _normalise_sha256(
            activator_cli_sha256,
            label="activator CLI SHA-256",
        ),
        "document_kind": CLEAN_STATE_ATTESTATION_DOCUMENT_KIND,
        "operator_attestation_locally_proven": False,
        "prior_marker_loss_or_incident_evidence_reconciled": True,
        "project_device": int(project_device),
        "project_inode": int(project_inode),
        "project_root": str(Path(project_root).resolve(strict=True)),
        "reconciliation_reference": str(reconciliation_reference),
        "refused_state_basenames": list(REFUSED_STATE_BASENAMES),
        "refused_state_inventory_sha256": _refused_state_inventory_sha256(),
        "refused_state_prefixes": list(REFUSED_STATE_PREFIXES),
        "schema_version": CLEAN_STATE_ATTESTATION_SCHEMA_VERSION,
        "unresolved_receipts_absent_after_external_review": True,
        "unresolved_transport_state_absent_after_external_review": True,
    }
    return _canonical_json_bytes(value)


def _load_clean_state_attestation(
    path: Path,
    *,
    expected_sha256: str,
    project: Path,
    project_identity: os.stat_result,
    activator_cli_sha256: str,
) -> _CleanStateAttestation:
    """Read and bind one immutable external operator attestation."""

    expected = _normalise_sha256(
        expected_sha256,
        label="expected clean-state attestation SHA-256",
    )
    supplied = Path(os.path.abspath(os.fspath(path)))
    if supplied == project or project in supplied.parents:
        raise ProtocolActivationRefused(
            "clean-state attestation must be external to the project root"
        )
    parent, parent_fd = _open_project_directory_without_symlinks(supplied.parent)
    descriptor: int | None = None
    try:
        descriptor, before = _open_verified_regular(
            parent_fd,
            supplied.name,
            label="external clean-state attestation",
            flags=os.O_RDONLY,
            require_single_link=True,
        )
        if (
            before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != CLEAN_STATE_ATTESTATION_MODE
            or before.st_size <= 0
            or before.st_size > MAX_CLEAN_STATE_ATTESTATION_BYTES
        ):
            raise ProtocolActivationRefused(
                "external clean-state attestation has unsafe metadata"
            )
        data = _read_descriptor_bytes(
            descriptor,
            maximum=MAX_CLEAN_STATE_ATTESTATION_BYTES,
            label="external clean-state attestation",
        )
        after_fd = os.fstat(descriptor)
        after_path = os.stat(
            supplied.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        _require_project_path_identity(parent, parent_fd, os.fstat(parent_fd))
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
        if any(
            getattr(before, field) != getattr(after_fd, field)
            or getattr(before, field) != getattr(after_path, field)
            for field in stable_fields
        ):
            raise ProtocolActivationRefused(
                "external clean-state attestation changed while inspected"
            )
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise ProtocolActivationRefused(
                "external clean-state attestation SHA-256 differs from the exact CLI value"
            )
        try:
            value = json.loads(
                data.decode("utf-8"),
                object_pairs_hook=_strict_json_object,
                parse_constant=_reject_json_constant,
            )
        except ProtocolActivationRefused:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolActivationRefused(
                "external clean-state attestation is not strict UTF-8 JSON"
            ) from exc
        if not isinstance(value, dict) or _canonical_json_bytes(value) != data:
            raise ProtocolActivationRefused(
                "external clean-state attestation is not canonical JSON"
            )
        required = {
            "active_markers_absent_after_external_review",
            "activator_cli_sha256",
            "document_kind",
            "operator_attestation_locally_proven",
            "prior_marker_loss_or_incident_evidence_reconciled",
            "project_device",
            "project_inode",
            "project_root",
            "reconciliation_reference",
            "refused_state_basenames",
            "refused_state_inventory_sha256",
            "refused_state_prefixes",
            "schema_version",
            "unresolved_receipts_absent_after_external_review",
            "unresolved_transport_state_absent_after_external_review",
        }
        reference = value.get("reconciliation_reference")
        if (
            set(value) != required
            or type(value.get("schema_version")) is not int
            or value.get("schema_version") != CLEAN_STATE_ATTESTATION_SCHEMA_VERSION
            or value.get("document_kind") != CLEAN_STATE_ATTESTATION_DOCUMENT_KIND
            or value.get("active_markers_absent_after_external_review") is not True
            or value.get("unresolved_receipts_absent_after_external_review") is not True
            or value.get("unresolved_transport_state_absent_after_external_review")
            is not True
            or value.get("prior_marker_loss_or_incident_evidence_reconciled") is not True
            or value.get("operator_attestation_locally_proven") is not False
            or value.get("activator_cli_sha256") != activator_cli_sha256
            or value.get("project_root") != str(project.resolve(strict=True))
            or type(value.get("project_device")) is not int
            or type(value.get("project_inode")) is not int
            or int(value.get("project_device", -1)) != int(project_identity.st_dev)
            or int(value.get("project_inode", -1)) != int(project_identity.st_ino)
            or value.get("refused_state_basenames")
            != list(REFUSED_STATE_BASENAMES)
            or value.get("refused_state_prefixes") != list(REFUSED_STATE_PREFIXES)
            or value.get("refused_state_inventory_sha256")
            != _refused_state_inventory_sha256()
            or not isinstance(reference, str)
            or not reference.strip()
            or len(reference) > 512
            or any(ord(character) < 0x20 for character in reference)
        ):
            raise ProtocolActivationRefused(
                "external clean-state attestation fields do not bind this activation"
            )
        return _CleanStateAttestation(
            source_path=str(supplied),
            data=data,
            sha256=actual,
            reconciliation_reference=reference,
        )
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)


def _entry_absent(directory_fd: int, basename: str) -> bool:
    """Return true only when one direct child has no namespace entry."""

    try:
        os.stat(basename, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    except OSError as exc:
        raise UnsafeReconciliationPathError(
            f"cannot safely inspect activation precondition: {basename}"
        ) from exc
    return False


def _activation_namespace_plan(directory_fd: int) -> _ActivationNamespacePlan:
    """Classify only safe complete or durably ordered activation states."""

    current_sentinel = not _entry_absent(directory_fd, ACTIVATION_BASENAME)
    current_audit = not _entry_absent(directory_fd, ACTIVATION_AUDIT_BASENAME)
    legacy_sentinel = not _entry_absent(
        directory_fd,
        LEGACY_ACTIVATION_BASENAME,
    )
    legacy_audit = not _entry_absent(
        directory_fd,
        LEGACY_ACTIVATION_AUDIT_BASENAME,
    )
    if (current_sentinel or current_audit) and (legacy_sentinel or legacy_audit):
        raise ProtocolActivationRefused(
            "torn dual v1/v2 protocol activation namespace is present"
        )
    if current_sentinel and not current_audit:
        raise ProtocolActivationRefused(
            "torn v2 activation sentinel exists without its audit"
        )
    if legacy_sentinel and not legacy_audit:
        raise ProtocolActivationRefused(
            "torn v1 activation sentinel exists without its audit"
        )
    if legacy_sentinel:
        try:
            legacy_snapshot = _inspect_legacy_protocol_activation_at(directory_fd)
        except ProtocolActivationError as exc:
            raise ProtocolActivationRefused(
                "legacy v1 activation pair is not valid for migration"
            ) from exc
        return _ActivationNamespacePlan(
            migrate_legacy=True,
            resume_after_legacy_sentinel_removal=False,
            reuse_current=False,
            legacy_sentinel_identity=(
                legacy_snapshot.device,
                legacy_snapshot.inode,
            ),
            legacy_audit_identity=(
                legacy_snapshot.audit_device,
                legacy_snapshot.audit_inode,
            ),
        )
    if legacy_audit:
        # The migrator removes and synchronises the v1 sentinel first.  A lone
        # exact audit is therefore the sole supported crash-resume residue.
        try:
            inspected = _inspect_stable_regular_at(
                directory_fd,
                LEGACY_ACTIVATION_AUDIT_BASENAME,
                expected_mode=ACTIVATION_AUDIT_MODE,
                maximum_size=16 * 1024,
                label="legacy remote-write protocol activation audit",
            )
            value = _parse_legacy_activation_audit(inspected.data)
            directory_identity = os.fstat(directory_fd)
            if (
                int(value["project_device"]) != int(directory_identity.st_dev)
                or int(value["project_inode"]) != int(directory_identity.st_ino)
            ):
                raise ProtocolActivationError(
                    "legacy activation audit does not bind this project"
                )
        except ProtocolActivationError as exc:
            raise ProtocolActivationRefused(
                "legacy audit-only migration residue is invalid"
            ) from exc
        return _ActivationNamespacePlan(
            migrate_legacy=True,
            resume_after_legacy_sentinel_removal=True,
            reuse_current=False,
            legacy_sentinel_identity=None,
            legacy_audit_identity=(
                int(inspected.metadata.st_dev),
                int(inspected.metadata.st_ino),
            ),
        )
    return _ActivationNamespacePlan(
        migrate_legacy=False,
        resume_after_legacy_sentinel_removal=False,
        reuse_current=current_sentinel,
        legacy_sentinel_identity=None,
        legacy_audit_identity=None,
    )


def _remove_legacy_activation_for_v2(
    directory_fd: int,
    plan: _ActivationNamespacePlan,
) -> None:
    """Durably disable v1 before any v2 permission sentinel is published."""

    if not plan.migrate_legacy:
        return
    if not plan.resume_after_legacy_sentinel_removal:
        current_sentinel = os.stat(
            LEGACY_ACTIVATION_BASENAME,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            int(current_sentinel.st_dev),
            int(current_sentinel.st_ino),
        ) != plan.legacy_sentinel_identity:
            raise ProtocolActivationRefused(
                "legacy activation sentinel changed before migration"
            )
        os.unlink(LEGACY_ACTIVATION_BASENAME, dir_fd=directory_fd)
        os.fsync(directory_fd)
    current_audit = os.stat(
        LEGACY_ACTIVATION_AUDIT_BASENAME,
        dir_fd=directory_fd,
        follow_symlinks=False,
    )
    if (
        int(current_audit.st_dev),
        int(current_audit.st_ino),
    ) != plan.legacy_audit_identity:
        raise ProtocolActivationRefused(
            "legacy activation audit changed before migration"
        )
    os.unlink(LEGACY_ACTIVATION_AUDIT_BASENAME, dir_fd=directory_fd)
    os.fsync(directory_fd)
    if not _entry_absent(
        directory_fd,
        LEGACY_ACTIVATION_BASENAME,
    ) or not _entry_absent(directory_fd, LEGACY_ACTIVATION_AUDIT_BASENAME):
        raise ProtocolActivationRefused(
            "legacy activation namespace survived v1-to-v2 migration"
        )


def _require_clean_state(directory_fd: int) -> None:
    """Refuse activation while any incident marker or receipt exists."""

    present_exact = [
        basename
        for basename in REFUSED_STATE_BASENAMES
        if not _entry_absent(directory_fd, basename)
    ]
    try:
        direct_names = os.listdir(directory_fd)
    except OSError as exc:
        raise UnsafeReconciliationPathError(
            "cannot enumerate the activation state namespace"
        ) from exc
    present_prefixed = sorted(
        name
        for name in direct_names
        if any(name.startswith(prefix) for prefix in REFUSED_STATE_PREFIXES)
    )
    present = sorted(set(present_exact) | set(present_prefixed))
    if present:
        raise ProtocolActivationRefused(
            "offline activation requires reconciled remote-write state; "
            "present entries: " + ", ".join(present)
        )


def _require_established_state(directory_fd: int) -> tuple[str, ...]:
    """Bind activation to the three durable files of an existing install."""

    for basename in ESTABLISHED_STATE_BASENAMES:
        descriptor, identity = _open_verified_regular(
            directory_fd,
            basename,
            label=f"established installation state {basename}",
            flags=os.O_RDONLY,
            require_single_link=True,
        )
        try:
            if identity.st_uid != os.geteuid():
                raise ProtocolActivationRefused(
                    f"established installation state is not owned by the current user: {basename}"
                )
        finally:
            os.close(descriptor)
    return ESTABLISHED_STATE_BASENAMES


def _require_expected_project_identity(
    project: Path,
    project_fd: int,
    *,
    expected_project_root: Path,
    expected_project_device: int,
    expected_project_inode: int,
) -> os.stat_result:
    """Require the mandatory preflight token to name this exact directory."""

    supplied_root = Path(
        os.path.abspath(os.fspath(expected_project_root))
    )
    try:
        resolved_project = project.resolve(strict=True)
        resolved_expected = supplied_root.resolve(strict=True)
    except OSError as exc:
        raise UnsafeReconciliationPathError(
            "expected project-root identity cannot be resolved"
        ) from exc
    identity = os.fstat(project_fd)
    if (
        resolved_project != resolved_expected
        or int(identity.st_dev) != int(expected_project_device)
        or int(identity.st_ino) != int(expected_project_inode)
    ):
        raise UnsafeReconciliationPathError(
            "project root differs from the mandatory preflight identity token"
        )
    return identity


def activate_protocol_offline(
    *,
    project_root: Path,
    expected_project_root: Path,
    expected_project_device: int,
    expected_project_inode: int,
    supervisor_stopped_confirmed: bool,
    clean_state_attestation_path: Path | None = None,
    expected_clean_state_attestation_sha256: str | None = None,
) -> ProtocolActivationResult:
    """Activate a stopped established install from one external attestation.

    Local locks and namespace checks are necessary but cannot prove that an
    absent marker was never lost or improperly removed.  The external file is
    an operator assertion: its exact bytes, CLI hash and project identity are
    verified, but the truth of its reconciliation claim is not locally proven.
    """

    if not supervisor_stopped_confirmed:
        raise ProtocolActivationRefused(
            "the wrapper/supervisor-stopped precondition was not declared"
        )
    if clean_state_attestation_path is None:
        raise ProtocolActivationRefused(
            "an external clean-state/reconciliation attestation is required"
        )
    if expected_clean_state_attestation_sha256 is None:
        raise ProtocolActivationRefused(
            "the exact clean-state attestation SHA-256 is required"
        )

    try:
        project, project_fd = _open_project_directory_without_symlinks(
            Path(project_root)
        )
    except OSError as exc:
        raise UnsafeReconciliationPathError(
            "project root could not be opened without following links"
        ) from exc
    instance_socket: socket.socket | None = None
    lock_fd: int | None = None
    try:
        try:
            fcntl.flock(project_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BotStillRunningError(
                "bot state-directory lock is held; stop the service first"
            ) from exc
        project_identity = _require_expected_project_identity(
            project,
            project_fd,
            expected_project_root=expected_project_root,
            expected_project_device=expected_project_device,
            expected_project_inode=expected_project_inode,
        )
        _require_project_path_identity(project, project_fd, project_identity)
        if not _descriptor_owns_exclusive_flock(
            project_fd,
            expected_device=int(project_identity.st_dev),
            expected_inode=int(project_identity.st_ino),
        ):
            raise BotStillRunningError(
                "state-directory flock acquisition could not be proved"
            )

        instance_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        socket_name = instance_lock_abstract_socket_name_for_identity(
            int(project_identity.st_dev),
            int(project_identity.st_ino),
        )
        try:
            instance_socket.bind(socket_name)
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                raise BotStillRunningError(
                    "bot process singleton is active; stop the service first"
                ) from exc
            raise

        lock_fd, lock_identity = _open_verified_regular(
            project_fd,
            LOCK_BASENAME,
            label="bot instance lock",
            flags=os.O_RDWR,
            require_single_link=True,
        )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.fcntl(
                lock_fd,
                fcntl.F_OFD_SETLK,
                _ofd_lock_record(fcntl.F_WRLCK),
            )
        except (BlockingIOError, OSError) as exc:
            if isinstance(exc, BlockingIOError) or getattr(exc, "errno", None) in {
                errno.EACCES,
                errno.EAGAIN,
            }:
                raise BotStillRunningError(
                    "bot instance lock is held; stop the service first"
                ) from exc
            raise

        _revalidate_locked_instance_lock(
            project_fd,
            lock_fd,
            lock_identity,
            instance_socket,
            socket_name,
        )
        established_state = _require_established_state(project_fd)
        _require_clean_state(project_fd)
        _require_project_path_identity(project, project_fd, project_identity)
        cli_sha256 = _stable_cli_sha256()
        external_attestation = _load_clean_state_attestation(
            Path(clean_state_attestation_path),
            expected_sha256=expected_clean_state_attestation_sha256,
            project=project,
            project_identity=project_identity,
            activator_cli_sha256=cli_sha256,
        )
        activation_audit_bytes = build_established_install_activation_audit_bytes(
            project_device=int(project_identity.st_dev),
            project_inode=int(project_identity.st_ino),
            clean_state_attestation_sha256=external_attestation.sha256,
            clean_state_attestation_size=len(external_attestation.data),
            activator_cli_sha256=cli_sha256,
            reconciliation_reference=external_attestation.reconciliation_reference,
        )
        namespace_plan = _activation_namespace_plan(project_fd)
        activation_reused_existing = namespace_plan.reuse_current
        _remove_legacy_activation_for_v2(project_fd, namespace_plan)
        created = _create_or_revalidate_protocol_activation_at(
            project_fd,
            activation_audit_bytes=activation_audit_bytes,
        )
        _revalidate_locked_instance_lock(
            project_fd,
            lock_fd,
            lock_identity,
            instance_socket,
            socket_name,
        )
        _require_project_path_identity(project, project_fd, project_identity)
        _require_expected_project_identity(
            project,
            project_fd,
            expected_project_root=expected_project_root,
            expected_project_device=expected_project_device,
            expected_project_inode=expected_project_inode,
        )
        _require_established_state(project_fd)
        _require_clean_state(project_fd)
        # Re-read the external path and require the same immutable bytes before
        # accepting the audit/sentinel pair.  The durable companion audit holds
        # the hash and operator reference; it does not claim the assertion was
        # locally established.
        repeated_attestation = _load_clean_state_attestation(
            Path(clean_state_attestation_path),
            expected_sha256=external_attestation.sha256,
            project=project,
            project_identity=project_identity,
            activator_cli_sha256=cli_sha256,
        )
        if repeated_attestation != external_attestation:
            raise ProtocolActivationRefused(
                "external clean-state attestation changed during activation"
            )
        final = _create_or_revalidate_protocol_activation_at(
            project_fd,
            activation_audit_bytes=activation_audit_bytes,
        )
        if final != created:
            raise ProtocolActivationRefused(
                "protocol activation identity changed before successful return"
            )
        _revalidate_locked_instance_lock(
            project_fd,
            lock_fd,
            lock_identity,
            instance_socket,
            socket_name,
        )
        _require_project_path_identity(project, project_fd, project_identity)
        return ProtocolActivationResult(
            schema_version=2,
            operation="offline_remote_write_safety_protocol_activation",
            protocol_version=PROTOCOL_VERSION,
            project_root=str(project),
            activation_path=ACTIVATION_BASENAME,
            activation_sha256=hashlib.sha256(ACTIVATION_BYTES).hexdigest(),
            activation_size=len(ACTIVATION_BYTES),
            activation_mode=oct(ACTIVATION_MODE),
            activation_device=int(final.device),
            activation_inode=int(final.inode),
            activation_reused_existing=activation_reused_existing,
            activation_migrated_from_protocol_version=(
                1 if namespace_plan.migrate_legacy else None
            ),
            activation_kind=final.activation_kind,
            activation_audit_path=ACTIVATION_AUDIT_BASENAME,
            activation_audit_sha256=final.audit_sha256,
            activation_audit_size=final.audit_size,
            expected_project_root=str(project.resolve(strict=True)),
            expected_project_device=int(project_identity.st_dev),
            expected_project_inode=int(project_identity.st_ino),
            established_state_files=established_state,
            bot_instance_lock_acquired=True,
            supervisor_stopped_precondition_declared=True,
            supervisor_stopped_locally_proved=False,
            clean_state_attestation_sha256=external_attestation.sha256,
            clean_state_attestation_size=len(external_attestation.data),
            clean_state_attestation_source_path=external_attestation.source_path,
            clean_state_attestation_stable_nofollow_read=True,
            activator_cli_sha256=cli_sha256,
            external_operator_attestation_used=True,
            operator_clean_state_claim_locally_proven=False,
            activation_audit_durable_before_sentinel=True,
            active_markers_absent=True,
            unresolved_receipts_absent=True,
            refused_state_basenames=REFUSED_STATE_BASENAMES,
            refused_state_prefixes=REFUSED_STATE_PREFIXES,
            refused_state_inventory_sha256=_refused_state_inventory_sha256(),
            legacy_activation_namespace_absent=True,
            successful_return_requires_exact_sentinel=True,
            rollback_to_protocol_unaware_runtime_prohibited=True,
        )
    except BaseException as exc:
        if isinstance(exc, MarkerReconciliationError):
            raise
        raise ProtocolActivationRefused("offline protocol activation failed") from exc
    finally:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
        if instance_socket is not None:
            try:
                instance_socket.close()
            except OSError:
                pass
        try:
            os.close(project_fd)
        except OSError:
            pass


def build_parser() -> argparse.ArgumentParser:
    """Build the clean-state offline activation CLI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--expected-project-root", required=True, type=Path)
    parser.add_argument("--expected-project-device", required=True, type=int)
    parser.add_argument("--expected-project-inode", required=True, type=int)
    parser.add_argument(
        "--clean-state-attestation",
        required=True,
        type=Path,
        help=(
            "Path outside the project root to an immutable 0400 operator "
            "clean-state/reconciliation attestation. The tool validates its "
            "bindings but cannot locally prove the operator claim."
        ),
    )
    parser.add_argument(
        "--clean-state-attestation-sha256",
        required=True,
        help="Exact SHA-256 of the externally supplied attestation bytes.",
    )
    parser.add_argument(
        "--confirm-clean-offline-activation",
        action="store_true",
        help="Acknowledge that marker and receipt reconciliation is complete.",
    )
    parser.add_argument(
        "--confirm-supervisor-stopped",
        action="store_true",
        help=(
            "Declare that the service wrapper/supervisor is stopped; local locks "
            "prove daemon exclusion but cannot prove supervisor policy."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one stopped, lock-bound activation."""

    args = build_parser().parse_args(argv)
    if not args.confirm_clean_offline_activation:
        print(
            "refusing activation without --confirm-clean-offline-activation",
            file=sys.stderr,
        )
        return 2
    if not args.confirm_supervisor_stopped:
        print(
            "refusing activation without --confirm-supervisor-stopped",
            file=sys.stderr,
        )
        return 2
    try:
        result = activate_protocol_offline(
            project_root=args.project_root,
            expected_project_root=args.expected_project_root,
            expected_project_device=args.expected_project_device,
            expected_project_inode=args.expected_project_inode,
            supervisor_stopped_confirmed=True,
            clean_state_attestation_path=args.clean_state_attestation,
            expected_clean_state_attestation_sha256=(
                args.clean_state_attestation_sha256
            ),
        )
    except MarkerReconciliationError as exc:
        print(f"protocol activation refused: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(
        json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

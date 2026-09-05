"""Read-only remote-write safety and reconciliation archive observations.

Callers supply project paths, the stable byte reader, exact-Decimal JSON parser,
reconciliation diagnostic formatter, clock and current snapshot callbacks.
Safety converts the project path and samples its clock before control/archive
inspection. Archive reads delegate through the supplied private-reader callback
so the coordinator retains its existing entry points and patch seams.

Protocol, retirement, transport and media inspectors remain lazy and read-only.
Import performs no runtime I/O or service initialisation; this module owns no
publication, recovery, report-window annotation or operational-health authority.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Dict, List, Optional, Tuple

from mrs_log_digest_values import (
    REMOTE_WRITE_RECEIPT_ROLE_LABELS,
    bounded_exception_status,
)


REMOTE_WRITE_MARKER_BASENAMES = (
    "ambiguous_post_outcome.json",
    "ambiguous_post_outcome.restart_barrier.json",
)
REMOTE_WRITE_SOURCE_RECEIPT_BASENAMES = (
    "regular_post_receipt.json",
    "meme_post_receipt.json",
    "confirmed_reply_receipt.json",
    "historical_context_reply_receipt.json",
)
REMOTE_WRITE_SOURCE_RECEIPT_ROLES = {
    "regular_post_receipt.json": "regular_quote_image_main_post",
    "meme_post_receipt.json": "daily_meme_main_post",
    "confirmed_reply_receipt.json": "conversational_confirmed_reply",
    "historical_context_reply_receipt.json": "historical_context_reply",
}
REMOTE_MEDIA_RECEIPT_BASENAME = "remote_media_upload_receipt.json"
REMOTE_MEDIA_FENCE_BASENAME = "remote_media_upload_receipt.json.fence.json"
REMOTE_TRANSPORT_JOURNAL_BASENAME = "remote_write_transport_journal.json"
REMOTE_TRANSPORT_FENCE_BASENAME = "remote_write_transport_fence.json"
REMOTE_WRITE_ARCHIVE_BASENAME = "remote_write_safety_marker_archive"
REMOTE_WRITE_SNAPSHOT_MAX_BYTES = 256 * 1024
RETIREMENT_SOURCE_IDENTITY_KEYS = frozenset(
    ("ctime_ns", "device", "inode", "link_count", "mode", "mtime_ns", "owner_uid", "size")
)


def _canonical_retirement_source_identity(value: Any) -> str:
    """Return the stable structural representation of one exact source identity."""

    if (
        not isinstance(value, dict)
        or frozenset(value) != RETIREMENT_SOURCE_IDENTITY_KEYS
        or any(type(value[key]) is not int for key in RETIREMENT_SOURCE_IDENTITY_KEYS)
    ):
        raise ValueError("retirement source identity is not a strict exact-file identity")
    return json.dumps(
        value, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def _safe_relative_project_path(project_dir: Path, value: Any) -> Optional[Path]:
    """Resolve one lexical direct-project relative path without escaping."""

    if not isinstance(value, str) or not value:
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    return Path(project_dir) / relative


def _read_readonly_archive_bytes(
    project_dir: Path,
    value: Any,
    *,
    read_bytes: Callable[..., bytes],
) -> bytes:
    """Read one direct project-relative, mode-0400 archive file."""

    path = _safe_relative_project_path(project_dir, value)
    if path is None:
        raise ValueError("archive path is not a safe project-relative path")
    if path.parent != Path(project_dir) / REMOTE_WRITE_ARCHIVE_BASENAME:
        raise ValueError("archive path is not a direct reconciliation-archive child")
    metadata = os.lstat(path)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o400
    ):
        raise ValueError("archive is not a mode-0400 regular file")
    return read_bytes(
        path,
        maximum=REMOTE_WRITE_SNAPSHOT_MAX_BYTES,
    )


def reconciliation_archive_snapshot(
    project_dir: Path,
    *,
    read_bytes: Callable[..., bytes],
    parse_json_object: Callable[..., Dict[str, Any]],
    inspection_error: Callable[[BaseException], str],
    read_archive_bytes: Callable[[Path, Any], bytes],
) -> Dict[str, Any]:
    """Validate durable marker/media audit receipts without changing them."""

    project_dir = Path(project_dir)
    archive = project_dir / REMOTE_WRITE_ARCHIVE_BASENAME
    try:
        metadata = os.lstat(archive)
    except FileNotFoundError:
        return {
            "present": False,
            "valid_marker_reconciliation_count": 0,
            "valid_media_reconciliation_count": 0,
            "marker_reconciliations": [],
            "media_reconciliations": [],
        }
    if not stat.S_ISDIR(metadata.st_mode):
        return {
            "present": True,
            "valid": False,
            "reason": "archive path is not a directory",
            "valid_marker_reconciliation_count": 0,
            "valid_media_reconciliation_count": 0,
            "marker_reconciliations": [],
            "media_reconciliations": [],
        }
    valid_marker: List[Dict[str, Any]] = []
    valid_media: List[Dict[str, Any]] = []
    invalid_audits: List[str] = []
    try:
        candidates = sorted(archive.glob("*.reconciliation.json"))
    except OSError as exc:
        return {
            "present": True,
            "valid": False,
            "reason": f"archive listing failed: {type(exc).__name__}",
            "valid_marker_reconciliation_count": 0,
            "valid_media_reconciliation_count": 0,
            "marker_reconciliations": [],
            "media_reconciliations": [],
        }
    for path in candidates:
        try:
            audit_metadata = os.lstat(path)
            if (
                not stat.S_ISREG(audit_metadata.st_mode)
                or stat.S_IMODE(audit_metadata.st_mode) != 0o400
            ):
                raise ValueError("audit is not a read-only regular file")
            data = read_bytes(
                path,
                maximum=REMOTE_WRITE_SNAPSHOT_MAX_BYTES,
            )
            value = parse_json_object(data, label=path.name)
            operation = value.get("operation")
            if operation == "offline_remote_write_safety_marker_archive":
                expected_hash = value.get("marker_sha256")
                archived_at_epoch = value.get("archived_at_epoch")
                reconciliation_reference = value.get("reconciliation_reference")
                marker_data = read_archive_bytes(
                    project_dir,
                    value.get("archive_path"),
                )
                if (
                    value.get("schema_version") != 3
                    or type(archived_at_epoch) is not int
                    or archived_at_epoch < 0
                    or not isinstance(reconciliation_reference, str)
                    or not reconciliation_reference.strip()
                    or len(reconciliation_reference) > 512
                    or not isinstance(expected_hash, str)
                    or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
                    or value.get("archive_and_receipt_durable_before_source_removal")
                    is not True
                    or value.get("restart_barrier_retired_last") is not True
                    or value.get("successful_return_requires_source_absent") is not True
                    or value.get(
                        "successful_return_requires_all_active_barriers_absent"
                    )
                    is not True
                    or hashlib.sha256(marker_data).hexdigest() != expected_hash
                ):
                    raise ValueError("marker audit/archive binding is invalid")
                marker_identity: Dict[str, Any] = {}
                try:
                    marker = parse_json_object(
                        marker_data,
                        label=str(value.get("archive_path") or "archived marker"),
                    )
                    marker_identity = {
                        "target_id": str(
                            marker.get("reply_to_id")
                            or marker.get("target_id")
                            or ""
                        ),
                        "transaction_id": str(
                            marker.get("transaction_id")
                            or marker.get("attempt_id")
                            or ""
                        ),
                    }
                except Exception as exc:
                    marker_identity["identity_error"] = (
                        inspection_error(exc)
                    )
                try:
                    resolution = parse_json_object(
                        read_archive_bytes(
                            project_dir,
                            reconciliation_reference,
                        ),
                        label=reconciliation_reference,
                    )
                    resolution_transaction_id = resolution.get("transaction_id")
                    resolution_target_id = resolution.get("target_id")
                    if (
                        resolution.get("schema_version") != 1
                        or resolution.get("operation")
                        != "offline_verified_definite_non_success_reply_archive"
                        or resolution.get("remote_disposition")
                        != "definite_non_success"
                        or resolution.get("audit_receipt_path")
                        != reconciliation_reference
                        or resolution.get("marker_sha256") != expected_hash
                        or not isinstance(resolution_transaction_id, str)
                        or re.fullmatch(
                            r"[0-9a-f]{64}", resolution_transaction_id
                        )
                        is None
                        or not isinstance(resolution_target_id, str)
                        or re.fullmatch(r"\d+", resolution_target_id) is None
                        or resolution.get(
                            "active_marker_preserved_after_transaction_reconciliation"
                        )
                        is not True
                        or resolution.get(
                            "successful_return_requires_active_transaction_pair_absent"
                        )
                        is not True
                        or (
                            marker_identity.get("target_id")
                            and marker_identity["target_id"]
                            != resolution_target_id
                        )
                        or (
                            marker_identity.get("transaction_id")
                            and marker_identity["transaction_id"]
                            != resolution_transaction_id
                        )
                    ):
                        raise ValueError(
                            "definite-non-success audit identity is invalid"
                        )
                    marker_identity.update(
                        {
                            "target_id": resolution_target_id,
                            "transaction_id": resolution_transaction_id,
                        }
                    )
                except Exception as exc:
                    marker_identity["reference_identity_error"] = (
                        inspection_error(exc)
                    )
                valid_marker.append(
                    {
                        "audit_path": str(path.relative_to(project_dir)),
                        "audit_sha256": hashlib.sha256(data).hexdigest(),
                        "archived_at_epoch": archived_at_epoch,
                        "marker_sha256": expected_hash,
                        "reconciliation_reference": reconciliation_reference,
                        **marker_identity,
                    }
                )
            elif operation == "offline_unattached_media_upload_archive":
                receipt_hash = value.get("receipt_sha256")
                fence_hash = value.get("fence_sha256")
                marker_hash = value.get("marker_sha256")
                transaction_id = value.get("media_transaction_id")
                archived_at_epoch = value.get("archived_at_epoch")
                image_basename = value.get("image_basename")
                if (
                    value.get("schema_version") != 1
                    or type(archived_at_epoch) is not int
                    or archived_at_epoch < 0
                    or not isinstance(transaction_id, str)
                    or re.fullmatch(r"[0-9a-f]{64}", transaction_id) is None
                    or not isinstance(receipt_hash, str)
                    or not isinstance(fence_hash, str)
                    or re.fullmatch(r"[0-9a-f]{64}", receipt_hash) is None
                    or re.fullmatch(r"[0-9a-f]{64}", fence_hash) is None
                    or not isinstance(marker_hash, str)
                    or re.fullmatch(r"[0-9a-f]{64}", marker_hash) is None
                    or not isinstance(image_basename, str)
                    or not image_basename
                    or Path(image_basename).name != image_basename
                    or value.get("accepted_media_disposition")
                    != "unattached_and_abandoned"
                    or value.get("no_tweet_create_authority_present") is not True
                    or value.get("operator_confirmed_no_tweet_create_attempted")
                    is not True
                    or value.get("operator_confirmed_unattached_media_abandoned")
                    is not True
                    or value.get("remote_media_id_absent") is not True
                    or value.get(
                        "media_archives_and_audit_durable_before_active_removal"
                    )
                    is not True
                    or value.get("media_receipt_retired_before_fence") is not True
                    or value.get("active_marker_preserved_after_media_reconciliation")
                    is not True
                    or value.get("successful_return_requires_active_media_pair_absent")
                    is not True
                    or hashlib.sha256(
                        read_archive_bytes(
                            project_dir,
                            value.get("receipt_archive_path"),
                        )
                    ).hexdigest()
                    != receipt_hash
                    or hashlib.sha256(
                        read_archive_bytes(
                            project_dir,
                            value.get("fence_archive_path"),
                        )
                    ).hexdigest()
                    != fence_hash
                ):
                    raise ValueError("media audit/archive binding is invalid")
                valid_media.append(
                    {
                        "audit_path": str(path.relative_to(project_dir)),
                        "audit_sha256": hashlib.sha256(data).hexdigest(),
                        "archived_at_epoch": archived_at_epoch,
                        "transaction_id": transaction_id,
                        "marker_sha256": marker_hash,
                        "image_basename": image_basename,
                        "accepted_media_disposition": value.get(
                            "accepted_media_disposition"
                        ),
                    }
                )
            else:
                continue
        except Exception as exc:
            invalid_audits.append(f"{path.name}: {type(exc).__name__}: {exc}")
    newest = lambda rows: max(
        rows,
        key=lambda item: (
            int(item.get("archived_at_epoch") or -1),
            str(item.get("audit_path") or ""),
        ),
        default=None,
    )
    return {
        "present": True,
        "valid": not invalid_audits,
        "valid_marker_reconciliation_count": len(valid_marker),
        "valid_media_reconciliation_count": len(valid_media),
        "marker_reconciliations": sorted(
            valid_marker,
            key=lambda item: (
                int(item.get("archived_at_epoch") or -1),
                str(item.get("audit_path") or ""),
            ),
        ),
        "media_reconciliations": sorted(
            valid_media,
            key=lambda item: (
                int(item.get("archived_at_epoch") or -1),
                str(item.get("audit_path") or ""),
            ),
        ),
        "latest_marker_reconciliation": newest(valid_marker),
        "latest_media_reconciliation": newest(valid_media),
        "invalid_audits": invalid_audits,
    }


def _remote_write_document_identity(
    document: Dict[str, Any],
) -> Dict[str, Any]:
    """Extract non-secret transaction identity from one active barrier document."""

    reply_context = document.get("reply_context")
    if not isinstance(reply_context, dict):
        reply_context = {}
    remote_payload = document.get("remote_payload")
    if not isinstance(remote_payload, dict):
        remote_payload = {}
    remote_reply = remote_payload.get("reply")
    if not isinstance(remote_reply, dict):
        remote_reply = {}
    source_receipt = document.get("source_receipt")
    if not isinstance(source_receipt, dict):
        source_receipt = {}

    def text_value(*values: Any) -> str:
        for value in values:
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                rendered = str(value).strip()
                if rendered:
                    return rendered
        return ""

    epochs = [
        value
        for value in (
            document.get("recorded_at_epoch"),
            document.get("attempt_epoch"),
            document.get("reply_epoch"),
            document.get("created_at_epoch"),
        )
        if type(value) is int and value >= 0
    ]
    return {
        "transaction_id": text_value(
            document.get("transaction_id"),
            document.get("attempt_id"),
            document.get("media_transaction_id"),
        ),
        "lane": text_value(
            document.get("lane"),
            document.get("candidate_source"),
            reply_context.get("lane"),
        ),
        "target_id": text_value(
            document.get("target_id"),
            document.get("reply_to_id"),
            remote_reply.get("in_reply_to_tweet_id"),
            reply_context.get("target_id"),
        ),
        "source_receipt_name": text_value(source_receipt.get("basename")),
        "source_receipt_sha256": text_value(source_receipt.get("sha256")),
        "recorded_at_epoch": min(epochs) if epochs else None,
    }


def _group_active_remote_write_artifacts(
    artifacts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Group active files only when their payload identities explicitly connect."""

    if not artifacts:
        return []
    artifacts = [dict(item) for item in artifacts]
    for item in artifacts:
        source_identity = item.get("retirement_source_identity")
        if source_identity is None:
            continue
        try:
            canonical = _canonical_retirement_source_identity(source_identity)
        except ValueError as exc:
            error = f"ValueError: {exc}"
            if item.get("identity_error"):
                error = str(item["identity_error"]) + "; " + error
            item["identity_error"] = error
            item.pop("retirement_source_identity", None)
            continue
        item["retirement_source_identity_canonical"] = canonical
        item["retirement_source_identity_sha256"] = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
    parents = list(range(len(artifacts)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = root(left)
        right_root = root(right)
        if left_root != right_root:
            parents[right_root] = left_root

    def component_values(index: int, *fields: str) -> set[str]:
        component = root(index)
        return {
            str(item.get(field) or "").strip()
            for item_index, item in enumerate(artifacts)
            if root(item_index) == component
            for field in fields
            if str(item.get(field) or "").strip()
        }

    def compatible(left: int, right: int) -> bool:
        def combined(*fields: str) -> set[str]:
            return component_values(left, *fields) | component_values(
                right, *fields
            )

        expected_or_bound_receipt = combined(
            "retirement_expected_sha256",
            "source_receipt_sha256",
        )
        return bool(
            len(combined("transaction_id")) <= 1
            and len(combined("target_id")) <= 1
            and len(combined("receipt_role")) <= 1
            and len(combined("retirement_source_basename")) <= 1
            and len(combined("retirement_expected_sha256")) <= 1
            and len(combined("retirement_expected_size")) <= 1
            and len(combined("retirement_source_identity_canonical")) <= 1
            and len(combined("source_receipt_name")) <= 1
            and len(combined("source_receipt_sha256")) <= 1
            and len(expected_or_bound_receipt) <= 1
        )

    def union_matching(fields: Tuple[str, ...]) -> None:
        for left in range(len(artifacts)):
            for right in range(left):
                if (
                    component_values(left, *fields)
                    & component_values(right, *fields)
                    and compatible(left, right)
                ):
                    union(left, right)

    # Transaction IDs are authoritative.  Exact document/source-receipt
    # hashes are the next strongest binding; filenames are deliberately not
    # identity because every transaction reuses the same small namespace.
    union_matching(("transaction_id",))
    union_matching(
        (
            "document_sha256",
            "artifact_sha256",
            "source_receipt_sha256",
            "retirement_expected_sha256",
        )
    )

    # Lane plus target is the final fallback.  A marker may omit its lane, so
    # permit that member only where the target has one compatible lane family
    # and no competing transaction ID.
    roots_by_target: Dict[str, set[int]] = {}
    for index, artifact in enumerate(artifacts):
        target_id = str(artifact.get("target_id") or "").strip()
        if target_id:
            roots_by_target.setdefault(target_id, set()).add(root(index))
    for target_roots in roots_by_target.values():
        transaction_ids = set().union(
            *(component_values(index, "transaction_id") for index in target_roots)
        )
        lanes = set().union(
            *(component_values(index, "lane") for index in target_roots)
        )
        specific_lanes = lanes - {"conversational_reply"}
        if len(transaction_ids) > 1 or len(specific_lanes) > 1 or not lanes:
            continue
        ordered_roots = sorted(target_roots)
        first_root = ordered_roots[0]
        for component_root in ordered_roots[1:]:
            if compatible(first_root, component_root):
                union(first_root, component_root)

    # Snapshot-only fallback: one malformed canonical auxiliary may join the
    # sole non-contradictory active generation for its exact source basename.
    roots_by_retirement_source: Dict[str, set[int]] = {}
    for index in range(len(artifacts)):
        if "receipt_retirement_auxiliary" not in component_values(index, "kind"):
            continue
        sources = component_values(index, "retirement_source_basename")
        if len(sources) == 1:
            roots_by_retirement_source.setdefault(next(iter(sources)), set()).add(
                root(index)
            )

    def values_for_roots(component_roots: set[int], *fields: str) -> set[str]:
        return {
            str(item.get(field) or "").strip()
            for item_index, item in enumerate(artifacts)
            if root(item_index) in component_roots
            for field in fields
            if str(item.get(field) or "").strip()
        }

    for component_roots in roots_by_retirement_source.values():
        if len(component_roots) <= 1:
            continue
        conflict_fields = (
            ("retirement_expected_sha256", "retirement_expected_sha256"),
            ("retirement_expected_size", "retirement_expected_size"),
            ("retirement_source_identity_canonical", "retirement_source_identity"),
            ("receipt_role", "receipt_role"),
            ("transaction_id", "transaction_id"),
            ("target_id", "target_id"),
            ("lane", "lane"),
            ("source_receipt_name", "source_receipt_binding"),
            ("source_receipt_sha256", "source_receipt_binding"),
        )
        conflict_reasons = {
            reason
            for field, reason in conflict_fields
            if len(values_for_roots(component_roots, field)) > 1
        }
        bound_receipt_hashes = values_for_roots(
            component_roots,
            "source_receipt_sha256",
        )
        if bound_receipt_hashes and len(
            bound_receipt_hashes
            | values_for_roots(
                component_roots,
                "retirement_expected_sha256",
            )
        ) > 1:
            conflict_reasons.add("source_receipt_binding")
        conflict_reasons = sorted(conflict_reasons)
        if conflict_reasons:
            for index, item in enumerate(artifacts):
                if root(index) in component_roots:
                    item["retirement_snapshot_conflict"] = True
                    item["retirement_conflict_reasons"] = conflict_reasons
            continue
        first_root, *other_roots = sorted(component_roots)
        for component_root in other_roots:
            union(first_root, component_root)

    components: Dict[int, List[Dict[str, Any]]] = {}
    for index, artifact in enumerate(artifacts):
        components.setdefault(root(index), []).append(artifact)

    grouped: List[Dict[str, Any]] = []
    for rows in components.values():
        def values(field: str) -> List[str]:
            return sorted({str(item[field]) for item in rows if item.get(field)})

        transaction_ids = values("transaction_id")
        target_ids = values("target_id")
        lanes = values("lane")
        recorded_epochs = [
            item.get("recorded_at_epoch")
            for item in rows
            if type(item.get("recorded_at_epoch")) is int
        ]
        receipt_roles = values("receipt_role")
        document_sha256s = values("document_sha256")
        artifact_sha256s = values("artifact_sha256")
        source_receipt_sha256s = values("source_receipt_sha256")
        retirement_expected_sha256s = values("retirement_expected_sha256")
        retirement_source_basenames = values("retirement_source_basename")
        retirement_phases = sorted(
            set(values("retirement_phase"))
            | set(values("retirement_document_phase"))
        )
        retirement_source_identity_canonicals = values(
            "retirement_source_identity_canonical"
        )
        retirement_source_identity_sha256s = values(
            "retirement_source_identity_sha256"
        )
        snapshot_identity_tokens = [
            *("document_sha256:" + value for value in document_sha256s),
            *("artifact_sha256:" + value for value in artifact_sha256s),
            *(
                "retirement_expected_sha256:"
                + source
                + ":"
                + expected
                for source in retirement_source_basenames
                for expected in retirement_expected_sha256s
            ),
            *(
                "retirement_source_identity:"
                + source
                + ":"
                + identity_sha256
                for source in retirement_source_basenames
                for identity_sha256 in retirement_source_identity_sha256s
            ),
            *(
                "source_receipt_sha256:" + value
                for value in source_receipt_sha256s
            ),
        ]
        invalid_artifact_names = sorted(
            {
                str(item.get("name"))
                for item in rows
                if item.get("safe_regular") is False
                or item.get("identity_error")
                or item.get("artifact_error")
            }
        )
        inspection_error_identities = sorted(
            {
                str(item.get("name") or "unavailable")
                + ":"
                + str(error).split(":", 1)[0]
                + ":"
                + hashlib.sha256(
                    str(error).encode("utf-8", errors="replace")
                ).hexdigest()
                for item in rows
                for error in (
                    item.get("identity_error"),
                    item.get("artifact_error"),
                    item.get("reason")
                    if item.get("safe_regular") is False
                    else None,
                )
                if error
            }
        )
        grouped.append(
            {
                "transaction_ids": transaction_ids,
                "target_ids": target_ids,
                "lanes": lanes,
                "artifact_names": values("name"),
                "artifact_kinds": values("kind"),
                "receipt_roles": receipt_roles,
                "receipt_role_labels": [
                    REMOTE_WRITE_RECEIPT_ROLE_LABELS.get(role, role)
                    for role in receipt_roles
                ],
                "transaction_states": values("transaction_state"),
                "invalid_artifact_names": invalid_artifact_names,
                "inspection_error_identities": inspection_error_identities,
                "document_sha256s": document_sha256s,
                "artifact_sha256s": artifact_sha256s,
                "source_receipt_sha256s": source_receipt_sha256s,
                "retirement_source_basenames": retirement_source_basenames,
                "retirement_expected_sha256s": retirement_expected_sha256s,
                "retirement_expected_sizes": sorted(
                    {
                        int(item["retirement_expected_size"])
                        for item in rows
                        if type(item.get("retirement_expected_size")) is int
                    }
                ),
                "retirement_phases": retirement_phases,
                "retirement_source_identities": [
                    json.loads(canonical)
                    for canonical in retirement_source_identity_canonicals
                ],
                "retirement_source_identity_sha256s": (
                    retirement_source_identity_sha256s
                ),
                "retirement_auxiliary_names": sorted(
                    {
                        str(item.get("name"))
                        for item in rows
                        if item.get("kind") == "receipt_retirement_auxiliary"
                        and item.get("name")
                    }
                ),
                "retirement_snapshot_conflict": any(
                    item.get("retirement_snapshot_conflict") is True
                    for item in rows
                ),
                "retirement_conflict_reasons": sorted(
                    {
                        str(reason)
                        for item in rows
                        for reason in item.get("retirement_conflict_reasons") or []
                    }
                ),
                "snapshot_identity_tokens": snapshot_identity_tokens,
                "recorded_at_epoch": min(recorded_epochs) if recorded_epochs else None,
                "attribution_identity_available": bool(
                    transaction_ids or target_ids or lanes
                ),
                "stable_incident_identity_available": bool(
                    document_sha256s
                    or artifact_sha256s
                    or source_receipt_sha256s
                    or retirement_expected_sha256s
                    or retirement_source_identity_sha256s
                    or any(item.get("name") for item in rows)
                ),
                # Retain the established field for JSON consumers while
                # keeping attribution separate from stable incident identity.
                "identity_available": bool(transaction_ids or target_ids or lanes),
            }
        )
    return sorted(
        grouped,
        key=lambda item: (
            item.get("recorded_at_epoch") if item.get("recorded_at_epoch") is not None else -1,
            item.get("transaction_ids") or [],
            item.get("target_ids") or [],
            item.get("artifact_names") or [],
        ),
    )


def remote_write_safety_snapshot(
    project_dir: Path,
    *,
    read_bytes: Callable[..., bytes],
    parse_json_object: Callable[..., Dict[str, Any]],
    now: Callable[[], datetime],
    control_snapshot: Callable[[Path], Dict[str, Any]],
    archive_snapshot: Callable[[Path], Dict[str, Any]],
) -> Dict[str, Any]:
    """Inspect every current v2 remote-write barrier without mutating state."""

    project_dir = Path(project_dir)
    observed_at = now().strftime("%Y-%m-%d %H:%M:%S")
    control = control_snapshot(project_dir)
    archive = archive_snapshot(project_dir)
    configured_names = [
        ".mrs_remote_write_safety_protocol_v2",
        ".mrs_remote_write_safety_protocol_v2.activation_audit.json",
        *REMOTE_WRITE_MARKER_BASENAMES,
        *REMOTE_WRITE_SOURCE_RECEIPT_BASENAMES,
        REMOTE_MEDIA_RECEIPT_BASENAME,
        REMOTE_MEDIA_FENCE_BASENAME,
        REMOTE_TRANSPORT_JOURNAL_BASENAME,
        REMOTE_TRANSPORT_FENCE_BASENAME,
        *(f".{name}.retirement.ledger.json" for name in REMOTE_WRITE_SOURCE_RECEIPT_BASENAMES),
    ]
    configured = control.get("present") is True or archive.get("present") is True
    for name in configured_names:
        try:
            os.lstat(project_dir / name)
        except FileNotFoundError:
            continue
        except OSError:
            configured = True
            break
        else:
            configured = True
            break
    if not configured:
        return {
            "configured": False,
            "available": False,
            "observed_at": observed_at,
            "status": "not_configured",
            "blocking": False,
            "control": control,
            "reconciliation_archive": archive,
        }

    active_entries: List[Dict[str, Any]] = []

    def observe_name(
        name: str,
        kind: str,
        *,
        receipt_role: Optional[str] = None,
        retirement_source_basename: str = "",
        retirement_path_phase: str = "",
    ) -> None:
        path = project_dir / name
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            return
        except OSError as exc:
            active_entries.append(
                {
                    "name": name,
                    "kind": kind,
                    "receipt_role": receipt_role,
                    "retirement_source_basename": retirement_source_basename,
                    "retirement_phase": retirement_path_phase,
                    "safe_regular": False,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
            return
        entry = {
            "name": name,
            "kind": kind,
            "receipt_role": receipt_role,
            "retirement_source_basename": retirement_source_basename,
            "retirement_phase": retirement_path_phase,
            "safe_regular": stat.S_ISREG(metadata.st_mode),
            "mode": oct(stat.S_IMODE(metadata.st_mode)),
            "size": int(metadata.st_size),
        }
        if entry["safe_regular"]:
            try:
                data = read_bytes(
                    path,
                    maximum=REMOTE_WRITE_SNAPSHOT_MAX_BYTES,
                )
                entry["artifact_sha256"] = hashlib.sha256(data).hexdigest()
                document = parse_json_object(data, label=name)
                entry.update(_remote_write_document_identity(document))
                entry["document_sha256"] = entry["artifact_sha256"]
                if kind == "receipt_retirement_auxiliary":
                    payload_source = document.get("source_basename")
                    if (
                        isinstance(payload_source, str)
                        and payload_source
                        and payload_source != retirement_source_basename
                    ):
                        raise ValueError(
                            "retirement source basename does not match its "
                            "canonical auxiliary path"
                        )
                    expected_sha256 = document.get("expected_sha256")
                    if (
                        isinstance(expected_sha256, str)
                        and re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
                    ):
                        entry["retirement_expected_sha256"] = expected_sha256
                    elif retirement_path_phase == "cleanup":
                        # A cleanup entry without a marker binding is the
                        # displaced exact source receipt itself.
                        entry["retirement_expected_sha256"] = entry[
                            "document_sha256"
                        ]
                    expected_size = document.get("expected_size")
                    if type(expected_size) is int and expected_size > 0:
                        entry["retirement_expected_size"] = expected_size
                    elif retirement_path_phase == "cleanup":
                        entry["retirement_expected_size"] = len(data)
                    phase = document.get("phase")
                    if isinstance(phase, str) and phase:
                        entry["retirement_document_phase"] = phase
                    source_identity = document.get("source_identity")
                    if source_identity is not None:
                        canonical_identity = (
                            _canonical_retirement_source_identity(source_identity)
                        )
                        entry["retirement_source_identity"] = source_identity
                        entry["retirement_source_identity_canonical"] = (
                            canonical_identity
                        )
                        entry["retirement_source_identity_sha256"] = (
                            hashlib.sha256(
                                canonical_identity.encode("utf-8")
                            ).hexdigest()
                        )
            except Exception as exc:
                entry["identity_error"] = bounded_exception_status(
                    "inspection failed",
                    exc,
                )
        active_entries.append(entry)

    for name in REMOTE_WRITE_MARKER_BASENAMES:
        observe_name(name, "ambiguity_marker")
    for name in REMOTE_WRITE_SOURCE_RECEIPT_BASENAMES:
        observe_name(
            name,
            "source_receipt",
            receipt_role=REMOTE_WRITE_SOURCE_RECEIPT_ROLES[name],
        )

    protocol: Dict[str, Any]
    try:
        from remote_write_safety_protocol import (
            ACTIVATION_BASENAME,
            inspect_protocol_activation,
        )

        activation = inspect_protocol_activation(project_dir / ACTIVATION_BASENAME)
        protocol = {
            "valid": True,
            "status": "active_v2",
            "activation_kind": activation.activation_kind,
            "sha256": activation.sha256,
            "audit_sha256": activation.audit_sha256,
        }
    except Exception as exc:
        protocol = {
            "valid": False,
            "status": "invalid_or_missing",
            "reason": f"{type(exc).__name__}: {exc}",
        }

    ledger_rows: List[Dict[str, Any]] = []
    retirement_auxiliaries: List[Dict[str, str]] = []
    try:
        from exact_receipt_retirement import (
            inspect_retirement_ledger,
            retirement_auxiliary_paths,
        )

        for name in REMOTE_WRITE_SOURCE_RECEIPT_BASENAMES:
            source = project_dir / name
            inspection = inspect_retirement_ledger(source)
            ledger_rows.append(
                {
                    "source": name,
                    "receipt_role": REMOTE_WRITE_SOURCE_RECEIPT_ROLES[name],
                    "valid": inspection.valid,
                    "blocking": inspection.blocking,
                    "state": inspection.state,
                    "sequence": inspection.sequence,
                    "record_sha256": inspection.record_sha256,
                    "detail": inspection.detail,
                    "ledger_path": inspection.ledger_path,
                    "exchange_path": inspection.exchange_path,
                }
            )
            ledger_row = ledger_rows[-1]
            if inspection.state in {"exchange_staged", "exchange_committed"}:
                binding_path = Path(
                    inspection.exchange_path
                    if inspection.state == "exchange_staged"
                    else inspection.ledger_path
                )
                try:
                    binding_data = read_bytes(
                        binding_path, maximum=REMOTE_WRITE_SNAPSHOT_MAX_BYTES
                    )
                    binding_document = parse_json_object(
                        binding_data, label=binding_path.name
                    )
                    binding = binding_document.get("source_binding")
                    if not isinstance(binding, dict):
                        raise ValueError("recoverable exchange has no source binding")
                    expected_sha256 = binding.get("expected_sha256")
                    expected_size = binding.get("expected_size")
                    source_identity = binding.get("source_identity")
                    if (
                        binding_document.get("source_basename") != name
                        or binding_document.get("state") != "completed"
                        or not isinstance(expected_sha256, str)
                        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
                        or type(expected_size) is not int
                        or expected_size <= 0
                    ):
                        raise ValueError("recoverable exchange binding is invalid")
                    canonical_identity = _canonical_retirement_source_identity(
                        source_identity
                    )
                    ledger_row.update(
                        {
                            "retirement_expected_sha256": expected_sha256,
                            "retirement_expected_size": expected_size,
                            "retirement_source_identity": source_identity,
                            "retirement_source_identity_sha256": hashlib.sha256(
                                canonical_identity.encode("utf-8")
                            ).hexdigest(),
                        }
                    )
                except Exception as exc:
                    ledger_row["binding_inspection_error"] = f"{type(exc).__name__}: {exc}"
            auxiliary_phases = (
                "prepared",
                "committed",
                "cleanup",
                "prepared",
                "committed",
            )
            for path, phase in zip(
                retirement_auxiliary_paths(source),
                auxiliary_phases,
            ):
                try:
                    os.lstat(path)
                except FileNotFoundError:
                    continue
                retirement_auxiliaries.append(
                    {
                        "name": path.name,
                        "source_basename": name,
                        "receipt_role": REMOTE_WRITE_SOURCE_RECEIPT_ROLES[name],
                        "phase": phase,
                    }
                )
    except Exception as exc:
        ledger_rows.append(
            {
                "source": "inspection",
                "valid": False,
                "blocking": True,
                "state": "unavailable",
                "sequence": -1,
                "record_sha256": "",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )
    for auxiliary in retirement_auxiliaries:
        observe_name(
            auxiliary["name"],
            "receipt_retirement_auxiliary",
            receipt_role=auxiliary["receipt_role"],
            retirement_source_basename=auxiliary["source_basename"],
            retirement_path_phase=auxiliary["phase"],
        )

    transport_artifact: Optional[Dict[str, Any]] = None
    try:
        from remote_write_transport_journal import inspect_transport_state

        transport_state = inspect_transport_state(
            project_dir / REMOTE_TRANSPORT_JOURNAL_BASENAME
        )
        transport = {
            "classification": transport_state.classification,
            "blocking": transport_state.blocking,
            "staging_names": list(transport_state.staging_names),
            "retirement_guard_names": list(
                transport_state.retirement_guard_names
            ),
            "errors": list(transport_state.errors),
        }
        transport_snapshot_name = REMOTE_TRANSPORT_JOURNAL_BASENAME
        transport_snapshot = transport_state.journal
        if transport_snapshot is None and transport_state.fence is not None:
            transport_snapshot_name = "remote_write_transport_fence.json"
            transport_snapshot = transport_state.fence
        if transport_snapshot is not None:
            transport_identity = _remote_write_document_identity(
                transport_snapshot.document
            )
            transport.update(transport_identity)
            transport["document_sha256"] = transport_snapshot.sha256
            transport_artifact = {
                "name": transport_snapshot_name,
                "kind": "transport_journal",
                "transaction_state": transport_state.classification,
                **transport_identity,
                "document_sha256": transport["document_sha256"],
            }
    except Exception as exc:
        transport = {
            "classification": "inspection_unavailable",
            "blocking": True,
            "staging_names": [],
            "retirement_guard_names": [],
            "errors": [f"{type(exc).__name__}: {exc}"],
        }

    media: Dict[str, Any]
    try:
        from remote_media_upload_receipt import (
            inspect_media_upload_receipt,
            media_upload_receipt_is_blocking,
        )

        media_path = project_dir / REMOTE_MEDIA_RECEIPT_BASENAME
        media_receipt = inspect_media_upload_receipt(media_path)
        media_blocking = media_upload_receipt_is_blocking(media_path)
        media = {
            "classification": (
                str(media_receipt.document.get("lifecycle_state") or "present")
                if media_receipt is not None
                else "blocking_companion_or_transition"
                if media_blocking
                else "clear"
            ),
            "blocking": media_blocking,
            "transaction_id": (
                media_receipt.document.get("transaction_id")
                if media_receipt is not None
                else None
            ),
            "lane": (
                media_receipt.document.get("lane")
                if media_receipt is not None
                else None
            ),
            "remote_media_id_present": bool(
                media_receipt is not None
                and media_receipt.document.get("remote_media_id")
            ),
        }
    except Exception as exc:
        media = {
            "classification": "invalid",
            "blocking": True,
            "transaction_id": None,
            "lane": None,
            "remote_media_id_present": False,
            "reason": f"{type(exc).__name__}: {exc}",
        }

    ledger_blocking = any(
        row.get("valid") is not True or row.get("blocking") is True
        for row in ledger_rows
    )
    blocking = bool(
        active_entries
        or protocol.get("valid") is not True
        or ledger_blocking
        or transport.get("blocking") is True
        or media.get("blocking") is True
    )
    global_pause = control.get("global_pause_active") is True
    control_invalid = control.get("valid") is not True
    status = (
        "blocked"
        if blocking
        else "paused_fail_closed_control"
        if control_invalid
        else "operator_paused"
        if global_pause
        else "ready"
    )
    active_marker_names = [
        item["name"]
        for item in active_entries
        if item.get("kind") == "ambiguity_marker"
    ]
    marker_reconciliation = archive.get("latest_marker_reconciliation") or {}
    media_reconciliation = archive.get("latest_media_reconciliation") or {}
    archive_valid = archive.get("valid") is True
    media_reconciliation_proven = bool(
        archive_valid
        and media.get("blocking") is False
        and media_reconciliation
        and (
            not marker_reconciliation
            or media_reconciliation.get("marker_sha256")
            == marker_reconciliation.get("marker_sha256")
        )
    )
    reconciliation_reference = marker_reconciliation.get(
        "reconciliation_reference"
    )
    media_reference_matches = bool(
        not isinstance(reconciliation_reference, str)
        or not reconciliation_reference.startswith(
            REMOTE_WRITE_ARCHIVE_BASENAME + "/unattached_media_upload."
        )
        or reconciliation_reference == media_reconciliation.get("audit_path")
    )

    def error_identity(value: Any) -> str:
        detail = str(value or "unavailable")
        return (detail.split(":", 1)[0].strip() or "unavailable") + ":" + hashlib.sha256(
            detail.encode("utf-8", errors="replace")
        ).hexdigest()

    snapshot_incident_evidence: List[Dict[str, Any]] = []

    def add_blocker(
        category: str,
        kind: str,
        identity: str,
        summary: str,
        **detail: Any,
    ) -> None:
        snapshot_incident_evidence.append(
            {
                "category": category,
                "blocker_kind": kind,
                "signature": f"snapshot:{kind.replace('_', '-')}:{identity}",
                "summary": summary,
                "snapshot_identity_tokens": [f"{kind}:{identity}"],
                **detail,
            }
        )

    if protocol.get("valid") is not True:
        protocol_identity = str(
            protocol.get("sha256")
            or protocol.get("audit_sha256")
            or error_identity(protocol.get("reason"))
        )
        add_blocker(
            "remote_write_protocol_barrier",
            "protocol_activation",
            str(protocol.get("status") or "invalid") + ":" + protocol_identity,
            "Remote-write protocol activation is invalid or missing: "
            + str(protocol.get("reason") or protocol.get("status") or "unavailable"),
            artifact_names=[
                ".mrs_remote_write_safety_protocol_v2",
                ".mrs_remote_write_safety_protocol_v2.activation_audit.json",
            ],
        )
    for ledger in ledger_rows:
        if ledger.get("valid") is True and ledger.get("blocking") is not True:
            continue
        source_basename = str(ledger.get("source") or "inspection")
        receipt_role = REMOTE_WRITE_SOURCE_RECEIPT_ROLES.get(source_basename, "")
        ledger_identity = str(
            ledger.get("record_sha256")
            or error_identity(ledger.get("detail"))
        )
        expected_sha256 = str(
            ledger.get("retirement_expected_sha256") or ""
        )
        source_identity_sha256 = str(
            ledger.get("retirement_source_identity_sha256") or ""
        )
        add_blocker(
            "remote_write_transaction_barrier",
            "retirement_ledger",
            source_basename + ":" + ledger_identity,
            "Permanent receipt-retirement ledger blocks remote writes: "
            f"{source_basename}, state {ledger.get('state') or 'unavailable'}, "
            f"sequence {ledger.get('sequence', 'unavailable')}, detail "
            + str(ledger.get("detail") or "unavailable"),
            retirement_source_basenames=[source_basename],
            retirement_expected_sha256s=[expected_sha256]
            if expected_sha256
            else [],
            retirement_expected_sizes=[ledger["retirement_expected_size"]]
            if type(ledger.get("retirement_expected_size")) is int
            else [],
            retirement_source_identities=[ledger["retirement_source_identity"]]
            if isinstance(ledger.get("retirement_source_identity"), dict)
            else [],
            retirement_source_identity_sha256s=[source_identity_sha256]
            if source_identity_sha256
            else [],
            receipt_roles=[receipt_role] if receipt_role else [],
            receipt_role_labels=[REMOTE_WRITE_RECEIPT_ROLE_LABELS[receipt_role]]
            if receipt_role
            else [],
            ledger_state=ledger.get("state"),
            ledger_sequence=ledger.get("sequence"),
            ledger_detail=ledger.get("detail"),
            ledger_record_sha256=ledger.get("record_sha256"),
            artifact_names=[
                Path(str(path)).name
                for path in (ledger.get("ledger_path"), ledger.get("exchange_path"))
                if path
            ],
        )
    if transport.get("classification") in {
        "inspection_unavailable",
        "directory_unavailable",
        "invalid",
    }:
        transport_errors = ",".join(transport.get("errors") or [])
        transport_identity = str(
            transport.get("document_sha256")
            or error_identity(transport_errors)
        )
        add_blocker(
            "remote_write_transaction_barrier",
            "transport_inspection",
            str(transport.get("classification") or "invalid") + ":" + transport_identity,
            "Remote-write transport-journal inspection failed: "
            + str(transport.get("classification") or "unavailable")
            + ("; " + transport_errors if transport_errors else ""),
            transaction_id=str(transport.get("transaction_id") or ""),
            lane=str(transport.get("lane") or ""),
            document_sha256s=[transport["document_sha256"]]
            if transport.get("document_sha256")
            else [],
            artifact_names=[REMOTE_TRANSPORT_JOURNAL_BASENAME, REMOTE_TRANSPORT_FENCE_BASENAME],
        )
    if media.get("classification") == "invalid" or media.get("reason"):
        media_identity = error_identity(media.get("reason"))
        add_blocker(
            "remote_write_transaction_barrier",
            "media_inspection",
            media_identity,
            "Remote-media receipt inspection failed: "
            + str(media.get("reason") or media.get("classification") or "unavailable"),
            transaction_id=str(media.get("transaction_id") or ""),
            lane=str(media.get("lane") or ""),
            artifact_names=[REMOTE_MEDIA_RECEIPT_BASENAME, REMOTE_MEDIA_FENCE_BASENAME],
        )
    identity_artifacts = [dict(item) for item in active_entries]
    if transport_artifact is not None and transport.get("blocking") is True:
        identity_artifacts.append(transport_artifact)
    if media.get("blocking") is True:
        identity_artifacts.append(
            {
                "name": REMOTE_MEDIA_RECEIPT_BASENAME,
                "kind": "media_receipt",
                "transaction_id": str(media.get("transaction_id") or ""),
                "lane": str(media.get("lane") or ""),
                "target_id": "",
            }
        )
    active_transaction_identities = _group_active_remote_write_artifacts(
        identity_artifacts
    )
    return {
        "configured": True,
        "available": True,
        "observed_at": observed_at,
        "status": status,
        "blocking": blocking,
        "ready_for_remote_writes": not blocking and not global_pause and not control_invalid,
        "active_entries": active_entries,
        "active_marker_names": active_marker_names,
        "active_transaction_identities": active_transaction_identities,
        "snapshot_incident_evidence": snapshot_incident_evidence,
        "identity_snapshot_available": True,
        "protocol": protocol,
        "retirement_ledgers": ledger_rows,
        "transport": transport,
        "media": media,
        "control": control,
        "reconciliation_archive": archive,
        "reconciliation_proven": bool(
            archive_valid
            and not active_marker_names
            and marker_reconciliation
            and media_reference_matches
        ),
        "media_reconciliation_proven": media_reconciliation_proven,
    }

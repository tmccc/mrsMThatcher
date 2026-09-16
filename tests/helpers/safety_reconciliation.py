"""Build stopped-bot incidents and CLI inputs for offline safety reconciliation."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys

import remote_media_upload_receipt as media_receipt
from tools import reconcile_remote_write_safety_marker as reconcile


MARKER_BYTES = b'{"outcome":"ambiguous_remote_post","schema_version":1}\n'


MARKER_SHA256 = hashlib.sha256(MARKER_BYTES).hexdigest()


STOPPED_DAEMON_PID = 2_147_483_647


def installation(tmp_path: Path) -> Path:
    """Create the minimal stopped-bot filesystem required by the tool."""

    project = tmp_path / "bot"
    project.mkdir()
    (project / reconcile.LOCK_BASENAME).write_text(
        f"pid={STOPPED_DAEMON_PID}\n",
        encoding="utf-8",
    )
    (project / reconcile.MARKER_BASENAME).write_bytes(MARKER_BYTES)
    return project


def media_incident_installation(
    tmp_path: Path,
) -> tuple[Path, bytes, media_receipt.MediaReceiptSnapshot, media_receipt.MediaReceiptSnapshot]:
    """Create the exact stopped, ambiguous pre-tweet media incident."""

    project = installation(tmp_path)
    marker_value = {
        "made_with_ai": False,
        "media_ids": [],
        "outcome": "ambiguous_remote_post",
        "recorded_at_epoch": 1_800_000_000,
        "reply_to_id": "",
        "schema_version": 1,
        "text_sha256": hashlib.sha256(b"").hexdigest(),
    }
    marker_bytes = reconcile._canonical_json_bytes(marker_value)
    marker = project / reconcile.MARKER_BASENAME
    marker.write_bytes(marker_bytes)
    os.link(marker, project / reconcile.RESTART_BARRIER_BASENAME)
    image = project / "reviewed-image.jpg"
    image.write_bytes(b"reviewed-image-bytes")
    image.chmod(0o600)
    receipt_path = project / reconcile.MEDIA_RECEIPT_BASENAME
    media_receipt.begin_media_upload(
        receipt_path=receipt_path,
        image_path=image,
        lane="quote_image",
        mime_type="image/jpeg",
        payload_metadata={
            "form": {
                "media_category": "tweet_image",
                "media_type": "image/jpeg",
            },
            "request_method": "POST",
            "request_path": "/2/media/upload",
        },
    )
    receipt = media_receipt.inspect_media_upload_receipt(receipt_path)
    assert receipt is not None
    fence = media_receipt._required_fence_snapshot(
        project / reconcile.MEDIA_FENCE_BASENAME
    )
    return project, marker_bytes, receipt, fence


def media_cli_command(
    project: Path,
    marker_bytes: bytes,
    receipt: media_receipt.MediaReceiptSnapshot,
    fence: media_receipt.MediaReceiptSnapshot,
) -> list[str]:
    """Build the complete reviewed-identity CLI invocation."""

    return [
        sys.executable,
        "tools/reconcile_remote_write_safety_marker.py",
        "--project-root",
        str(project),
        "--expected-marker-sha256",
        hashlib.sha256(marker_bytes).hexdigest(),
        "--reconcile-unattached-media-upload",
        "--expected-media-receipt-sha256",
        receipt.sha256,
        "--expected-media-fence-sha256",
        fence.sha256,
        "--expected-media-transaction-id",
        str(receipt.document["transaction_id"]),
        "--expected-media-receipt-device",
        str(receipt.device),
        "--expected-media-receipt-inode",
        str(receipt.inode),
        "--expected-media-receipt-ctime-ns",
        str(receipt.ctime_ns),
        "--expected-media-fence-device",
        str(fence.device),
        "--expected-media-fence-inode",
        str(fence.inode),
        "--expected-media-fence-ctime-ns",
        str(fence.ctime_ns),
        "--reconciliation-reference",
        "operator-reviewed-x-503-without-tweet-create",
    ]

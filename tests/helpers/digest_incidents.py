"""Synthetic remote-write reconciliation snapshots and media-incident records."""

from __future__ import annotations

from datetime import timedelta

import mrs_log_digest as digest

from tests.helpers.digest_records import BASE, record, traceback


def reconciled_remote_write_safety(*, archive_offset: int = 120) -> dict:
    """Return a current clear snapshot with evidence tied to t64.jpg."""

    archive_epoch = int((BASE + timedelta(seconds=archive_offset)).timestamp())
    marker_audit = {
        "archived_at_epoch": archive_epoch,
        "audit_path": "archive/marker.reconciliation.json",
        "marker_sha256": "a" * 64,
    }
    return {
        "configured": True,
        "available": True,
        "status": "operator_paused",
        "blocking": False,
        "ready_for_remote_writes": False,
        "protocol": {"valid": True},
        "control": {
            "valid": True,
            "generation": 3,
            "active_keys": ["disable_all"],
            "global_pause_active": True,
        },
        "reconciliation_proven": True,
        "media_reconciliation_proven": True,
        "reconciliation_archive": {
            "valid": True,
            "valid_marker_reconciliation_count": 1,
            "valid_media_reconciliation_count": 1,
            "marker_reconciliations": [marker_audit],
            "latest_marker_reconciliation": marker_audit,
            "latest_media_reconciliation": {
                "archived_at_epoch": archive_epoch - 1,
                "audit_path": "archive/media.reconciliation.json",
                "image_basename": "t64.jpg",
            },
        },
        "transport": {"classification": "clear", "blocking": False},
        "media": {"classification": "clear", "blocking": False},
        "retirement_ledgers": [
            {"valid": True, "blocking": False} for _ in range(4)
        ],
        "active_entries": [],
        "active_marker_names": [],
    }


def ambiguous_media_records() -> list[digest.Record]:
    """Represent the production receipt-bound media 503 cascade."""

    return [
        record(
            0,
            "INFO",
            "upload_media_v2",
            "Uploading receipt-bound media via X API v2: t64.jpg",
        ),
        record(
            1,
            "DEBUG",
            "x_request",
            "X request: POST https://api.x.com/2/media/upload",
        ),
        record(
            2,
            "ERROR",
            "x_request",
            'X API error 503: {"detail":"Service Unavailable","status":503}',
        ),
        record(
            3,
            "CRITICAL",
            "upload_media",
            "X media upload outcome is ambiguous; blocking every subsequent "
            "remote write pending manual reconciliation. image=t64.jpg",
        ),
        record(
            4,
            "CRITICAL",
            "record_ambiguous_remote_post",
            "AMBIGUOUS REMOTE X POST OUTCOME: X may have accepted the write, "
            "but a usable confirmation was not received. Automatic posting is "
            "blocked pending manual reconciliation: /srv/ambiguous_post_outcome.json",
        ),
        record(
            5,
            "ERROR",
            "main",
            traceback(
                "Quote/image remote outcome is ambiguous; the remote-write "
                "safety barrier is active and no retry will be scheduled",
                "AmbiguousRemotePostOutcome: X write outcome is not proved by "
                "HTTP status alone; received HTTP 503",
            ),
        ),
        record(
            6,
            "CRITICAL",
            "maintain_global_remote_write_barrier_tick",
            "All remote posting and reply lanes are paused by the durable "
            "remote-write safety barrier; manual reconciliation is required "
            "before a controlled restart",
        ),
    ]

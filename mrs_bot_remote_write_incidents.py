"""Remote-write incident latching and durable marker acknowledgement.

Marker encoding and hashing use local fixed operations. The root supplies
current clock, logging, latches and persistence boundaries on each call. This
module performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def durable_remote_write_safety_marker_exists(
    *,
    _set_ambiguous_marker_durability_uncertain: Any,
    acknowledge_durable_remote_write_safety_marker: Any,
    latch_remote_write_safety_marker_observation: Any,
    log: Any,
    release_retained_sigint_deferral_after_durable_barrier: Any,
    remote_write_safety_marker_path_present_or_unsafe: Any,
    remote_write_safety_protocol_is_active: Any,
) -> bool:
    """Return whether restart safety survives loss of the in-process latch."""
    if not remote_write_safety_protocol_is_active():
        return False
    if not remote_write_safety_marker_path_present_or_unsafe():
        return False

    # Once a namespace entry is observed, only an unchanged, ordinary,
    # no-follow marker may clear uncertainty or release a retained SIGINT.
    latch_remote_write_safety_marker_observation()
    try:
        acknowledge_durable_remote_write_safety_marker()
    except Exception:
        log.critical(
            "The remote-write safety marker cannot be durably acknowledged; "
            "the process-local latch and deferred SIGINT remain active",
            exc_info=True,
        )
        return False
    _set_ambiguous_marker_durability_uncertain(False)
    release_retained_sigint_deferral_after_durable_barrier()
    return True


def record_ambiguous_remote_post(
    payload: dict,
    *,
    AMBIGUOUS_POST_OUTCOME_FILE: Any,
    _set_ambiguous_marker_durability_uncertain: Any,
    _set_ambiguous_remote_post_seen: Any,
    ensure_durable_remote_write_safety_marker: Any,
    log: Any,
    now_epoch: Any,
) -> None:
    """Persist a manual-reconciliation barrier without claiming success or failure."""
    _set_ambiguous_remote_post_seen(True)
    _set_ambiguous_marker_durability_uncertain(True)
    text = str(payload.get("text") or "")
    try:
        marker = {
            "schema_version": 1,
            "recorded_at_epoch": now_epoch(),
            "outcome": "ambiguous_remote_post",
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "reply_to_id": str((payload.get("reply") or {}).get("in_reply_to_tweet_id") or ""),
            "media_ids": list((payload.get("media") or {}).get("media_ids") or []),
            "made_with_ai": bool(payload.get("made_with_ai")),
        }
        ensure_durable_remote_write_safety_marker(marker)
    except Exception:
        log.critical(
            "AMBIGUOUS REMOTE X POST OUTCOME: the durable safety marker could not be "
            "written. The process-local latch remains active; do not restart this "
            "process before manual reconciliation.",
            exc_info=True,
        )
        return
    _set_ambiguous_marker_durability_uncertain(False)
    log.critical(
        "AMBIGUOUS REMOTE X POST OUTCOME: X may have accepted the write, but a usable confirmation was not received. "
        "Automatic posting is blocked pending manual reconciliation: %s",
        AMBIGUOUS_POST_OUTCOME_FILE,
    )


def latch_confirmed_post_persistence_failure(
    *,
    lane: str,
    post_id: str,
    failure_components: list[str],
    AMBIGUOUS_POST_OUTCOME_FILE: Any,
    _set_ambiguous_marker_durability_uncertain: Any,
    _set_ambiguous_remote_post_seen: Any,
    ensure_durable_remote_write_safety_marker: Any,
    log: Any,
    now_epoch: Any,
) -> bool:
    """Block all writes after a confirmed post loses every complete recovery path.

    The in-process latch is set before any file operation.  This is essential
    when the durability failure is caused by an unwritable filesystem: exiting
    would let the service wrapper restart without a durable record and risk a
    duplicate post.

    Return whether the durable barrier marker was written (or already exists).
    """
    _set_ambiguous_remote_post_seen(True)
    _set_ambiguous_marker_durability_uncertain(True)
    failures = sorted({str(item) for item in failure_components if str(item)})
    incident_identity = json.dumps(
        {
            "failure_components": failures,
            "lane": str(lane),
            "post_id": str(post_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        recorded_at_epoch: int | None = now_epoch()
    except Exception:
        recorded_at_epoch = None
    marker = {
        "schema_version": 1,
        "outcome": "confirmed_remote_post_local_persistence_failed",
        "lane": str(lane),
        "post_id": str(post_id),
        "failure_components": failures,
        "incident_sha256": hashlib.sha256(incident_identity.encode("utf-8")).hexdigest(),
    }
    if recorded_at_epoch is None:
        marker["recorded_at_unavailable"] = True
    else:
        marker["recorded_at_epoch"] = recorded_at_epoch
    try:
        ensure_durable_remote_write_safety_marker(marker)
    except Exception:
        log.critical(
            "CONFIRMED REMOTE POST LOST COMPLETE LOCAL RECOVERY: post_id=%s lane=%s "
            "failures=%s. The durable safety marker could not be written. The "
            "process-local latch remains active; do not restart this process before "
            "manual reconciliation.",
            post_id,
            lane,
            failures,
            exc_info=True,
        )
        return False
    _set_ambiguous_marker_durability_uncertain(False)
    log.critical(
        "CONFIRMED REMOTE POST LOST COMPLETE LOCAL RECOVERY: post_id=%s lane=%s "
        "failures=%s. All remote writes are blocked pending manual reconciliation: %s",
        post_id,
        lane,
        failures,
        AMBIGUOUS_POST_OUTCOME_FILE,
    )
    return True

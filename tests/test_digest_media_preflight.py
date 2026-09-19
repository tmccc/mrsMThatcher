"""Distinguish local image checks from durable remote-write ambiguity."""

from __future__ import annotations

from datetime import timedelta

import pytest

import mrs_log_digest as digest

from tests.helpers.digest_records import BASE, record


IMAGE = "/srv/memes/090_image.png"


def legacy_source_read_failure(header: str = "Daily meme stage failed") -> str:
    """Retain the exact causal frame shape logged on 19 September 2026."""
    return f'''{header}
Traceback (most recent call last):
  File "/srv/mrs_bot_post_creation.py", line 150, in upload_media
    authority = begin_media_upload(
  File "/srv/remote_media_upload_receipt.py", line 1061, in begin_media_upload
    image = _read_stable_regular(image_path, maximum=IMAGE_MAX_BYTES)
  File "/srv/remote_media_upload_receipt.py", line 318, in _read_stable_regular
    directory_fd = _open_directory(path.parent)
  File "/srv/remote_media_upload_receipt.py", line 489, in _open_directory
    raise MediaUploadReceiptError("unsafe durable transaction directory") from exc
remote_media_upload_receipt.MediaUploadReceiptError: unsafe durable transaction directory

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/srv/mrs_bot_daily_meme.py", line 620, in <lambda>
    lambda: upload_media(str(meme_path), lane="daily_meme"),
  File "/srv/mrs_bot_post_creation.py", line 166, in upload_media
    raise AmbiguousRemotePostOutcome(
AmbiguousRemotePostOutcome: Could not establish the restart-persistent media-upload receipt'''


def typed_failure(lane: str = "daily_meme") -> str:
    """Represent the new error type with its lane and local-only boundary."""
    return (
        "Image posting failed\n"
        "remote_media_upload_receipt.MediaUploadPreflightError: "
        "Source image preflight failed before media receipt publication "
        f"(lane={lane} image='090_image.png'): unsafe durable transaction directory"
    )


def test_legacy_seventeen_retries_are_one_resolved_local_incident():
    records = []
    for minute in range(17):
        offset = minute * 60
        records.extend([
            record(offset, "INFO", "post_next_meme", f"Posting meme image: {IMAGE}"),
            record(offset, "ERROR", "run_daily_meme_stage", legacy_source_read_failure()),
            record(offset, "ERROR", "_run_due_meme_post_for_tick", legacy_source_read_failure(
                "Daily meme remote outcome is ambiguous; the remote-write safety barrier "
                "is active and no retry will be scheduled"), line=2),
        ])
    recovery_offset = 17 * 60 + 12
    records.append(record(recovery_offset, "INFO", "post_next_meme",
                          "Daily meme posted successfully. posted_id=2101310477565301129 "
                          "file=090_image.png"))

    health = digest.analyse(records)["error_health"]

    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1
    incident = health["historical_resolved_incidents"][0]
    assert incident["category"] == "local_media_preflight_failure"
    assert incident["record_count"] == health["raw_serious_error_record_count"] == 34
    assert incident["traceback_count"] == 34
    assert incident["resolution_time"] == digest.dt_text(BASE + timedelta(seconds=recovery_offset))
    assert "before receipt publication or upload" in incident["summary"]


@pytest.mark.parametrize("lane, success_kind", [
    ("daily_meme", "daily_meme_posted"), ("quote_image", "quote_image_posted"),
])
def test_typed_failure_requires_later_publication_in_its_lane(lane, success_kind):
    errors = [{"level": "ERROR", "time": digest.dt_text(BASE), "message": typed_failure(lane)}]
    wrong_kind = "quote_image_posted" if lane == "daily_meme" else "daily_meme_posted"
    events = [{"kind": wrong_kind, "time": digest.dt_text(BASE + timedelta(seconds=1)),
               "image_basename": "090_image.png"},
              {"kind": success_kind, "time": digest.dt_text(BASE),
               "image_basename": "090_image.png"}]
    health = digest.summarise_operational_error_health(errors, events, [])
    assert health["current_incidents"][0]["category"] == "local_media_preflight_failure"
    events.append({"kind": success_kind, "time": digest.dt_text(BASE + timedelta(seconds=2)),
                   "image_basename": "090_image.png"})
    health = digest.summarise_operational_error_health(errors, events, [])
    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incidents"][0]["resolution_time"] == events[-1]["time"]


def test_different_image_publication_does_not_resolve_known_failed_source():
    records = [
        record(0, "INFO", "post_next_meme", f"Posting meme image: {IMAGE}"),
        record(0, "ERROR", "run_daily_meme_stage", legacy_source_read_failure()),
        record(60, "INFO", "post_next_meme", "Posting meme image: /srv/memes/another.png"),
        record(61, "INFO", "post_next_meme", "Daily meme posted successfully. posted_id=123 file=another.png"),
    ]
    health = digest.analyse(records)["error_health"]
    assert health["current_incidents"][0]["category"] == "local_media_preflight_failure"
    assert health["historical_resolved_incident_count"] == 0


@pytest.mark.parametrize("changed", [
    # The same local helper also reads already-published receipts and fences.
    legacy_source_read_failure().replace(
        "image = _read_stable_regular(image_path, maximum=IMAGE_MAX_BYTES)",
        "snapshot = _read_stable_regular(receipt_path, maximum=RECEIPT_MAX_BYTES)"),
    legacy_source_read_failure().replace(
        "image = _read_stable_regular(image_path, maximum=IMAGE_MAX_BYTES)",
        "_publish_new(receipt_path, data)"),
    legacy_source_read_failure().replace("in begin_media_upload", "in bind_media_upload_payload"),
    legacy_source_read_failure().replace(
        "AmbiguousRemotePostOutcome: Could not establish the restart-persistent media-upload receipt",
        "AmbiguousRemotePostOutcome: Media upload outcome is ambiguous"),
    "AmbiguousRemotePostOutcome: Could not establish the restart-persistent media-upload receipt",
    # A typed preflight exception in an earlier handled cause must not override
    # the later authoritative ambiguity exception.
    typed_failure() + "\nAmbiguousRemotePostOutcome: Media upload outcome is ambiguous",
])
def test_receipt_establishment_and_true_remote_ambiguity_keep_barrier(changed):
    assert digest.classify_operational_error(changed) == "remote_write_ambiguity_barrier"
    health = digest.summarise_operational_error_health(
        [{"level": "ERROR", "time": digest.dt_text(BASE), "message": changed}],
        [{"kind": "daily_meme_posted", "time": digest.dt_text(BASE + timedelta(seconds=60))}],
        [],
    )
    assert health["current_incidents"][0]["category"] == "remote_write_ambiguity_barrier"
    assert health["historical_resolved_incident_count"] == 0


def test_unscoped_preflight_is_not_resolved_by_an_arbitrary_post():
    health = digest.summarise_operational_error_health(
        [{"level": "ERROR", "time": digest.dt_text(BASE),
          "message": "MediaUploadPreflightError: source image is not durably present"}],
        [{"kind": "daily_meme_posted", "time": digest.dt_text(BASE + timedelta(seconds=60))}],
        [],
    )
    assert health["current_incidents"][0]["category"] == "local_media_preflight_failure"


def test_preflight_without_source_identity_is_not_resolved_by_arbitrary_lane_success():
    health = digest.summarise_operational_error_health(
        [{"level": "ERROR", "time": digest.dt_text(BASE),
          "message": "Daily meme failed\nMediaUploadPreflightError: source image is not durably present"}],
        [{"kind": "daily_meme_posted", "time": digest.dt_text(BASE + timedelta(seconds=60)),
          "file": "090_image.png"}], [],
    )
    assert health["current_incidents"][0]["category"] == "local_media_preflight_failure"


def test_explicit_typed_image_overrides_stale_pending_source_and_keeps_escaped_name():
    image = "has ' spaces.png"
    message = typed_failure().replace("image='090_image.png'", f"image={image!r}")
    records = [record(0, "INFO", "post_next_meme", "Posting meme image: /srv/stale.png"),
               record(1, "ERROR", "run_daily_meme_stage", message),
               record(2, "INFO", "post_next_meme",
                      f"Daily meme posted successfully. posted_id=123 file={image}")]
    health = digest.analyse(records)["error_health"]
    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1

"""Own native reply-media attachment and bounded target/quoted photo selection.

Two root adapters supply the current photo cap, candidate callback and logger;
the dependency-free attachment helper is a direct root alias. Original bodies
preserve expansion record references, media accounting, ordering and native errors.
Discovery, context construction, image fetching/validation and persistence retain
their existing authority. This owner retains no callbacks, configuration, clients
or state and performs no import-time file, environment, provider or RNG work.
"""

from __future__ import annotations

from collections.abc import Callable
from logging import Logger


def attach_media_to_tweets(tweets: list[dict], includes: dict | None) -> None:
    """Attach media to tweets."""
    media_items = (includes or {}).get("media", [])
    if not isinstance(media_items, list):
        return

    media_by_key = {
        str(media.get("media_key")): media
        for media in media_items
        if isinstance(media, dict) and media.get("media_key")
    }
    if not media_by_key:
        return

    for tweet in tweets:
        attachments = tweet.get("attachments", {})
        if not isinstance(attachments, dict):
            continue
        media_keys = attachments.get("media_keys", [])
        if not isinstance(media_keys, list):
            continue
        attached = [
            media_by_key[str(media_key)]
            for media_key in media_keys
            if str(media_key) in media_by_key
        ]
        if attached:
            tweet["_attached_media"] = attached


def candidate_native_photo_media(
    candidate: dict,
    *,
    MAX_REPLY_CONTEXT_PHOTOS: int,
) -> tuple[list[dict], int]:
    """Return the candidate native photo media."""
    media_items = candidate.get("_attached_media", [])
    if not isinstance(media_items, list):
        media_items = []

    attachments = candidate.get("attachments")
    declared_media_keys: set[str] = set()
    malformed_attachment_metadata = False
    if attachments is not None:
        if not isinstance(attachments, dict):
            malformed_attachment_metadata = True
        else:
            raw_media_keys = attachments.get("media_keys", [])
            if not isinstance(raw_media_keys, list):
                malformed_attachment_metadata = True
            else:
                declared_media_keys = {
                    str(media_key).strip()
                    for media_key in raw_media_keys
                    if str(media_key).strip()
                }

    attached_media_keys = {
        str(media.get("media_key") or "").strip()
        for media in media_items
        if isinstance(media, dict)
        and str(media.get("media_key") or "").strip()
    }
    unresolved_declared_keys = declared_media_keys - attached_media_keys
    unclassified_declared_keys = {
        str(media.get("media_key") or "").strip()
        for media in media_items
        if (
            isinstance(media, dict)
            and str(media.get("media_key") or "").strip()
            in declared_media_keys
            and str(media.get("type") or "").lower()
            not in {"photo", "video", "animated_gif"}
        )
    }

    photo_records = [
        media
        for media in media_items
        if isinstance(media, dict) and str(media.get("type", "")).lower() == "photo"
    ]
    usable: list[dict] = []
    for media in photo_records:
        url = str(media.get("url") or "").strip()
        if not url:
            continue
        usable.append(
            {
                "media_key": str(media.get("media_key", "")),
                "url": url,
            }
        )
        if len(usable) >= MAX_REPLY_CONTEXT_PHOTOS:
            break

    unresolved_count = len(
        unresolved_declared_keys | unclassified_declared_keys
    )
    if malformed_attachment_metadata:
        unresolved_count = max(1, unresolved_count)
    return usable, len(photo_records) + unresolved_count


def reply_media_context_for_candidate(
    candidate: dict,
    *,
    lane: str,
    target_id: str,
    quoted_candidate: dict | None = None,
    MAX_REPLY_CONTEXT_PHOTOS: int,
    candidate_native_photo_media: Callable,
    log: Logger,
) -> dict:
    """Prioritise target photos, then photos from the directly quoted post."""

    target_photos, target_expected = candidate_native_photo_media(candidate)
    target_required = min(target_expected, MAX_REPLY_CONTEXT_PHOTOS)
    if len(target_photos) < target_required:
        selected: list[dict] = []
        metadata_complete = False
    else:
        selected = [
            {
                **photo,
                "attachment_role": "target_contribution",
                "source_post_id": str(candidate.get("id") or target_id),
            }
            for photo in target_photos[:MAX_REPLY_CONTEXT_PHOTOS]
        ]
        metadata_complete = True

    quoted_expected = 0
    quoted_photos: list[dict] = []
    remaining = MAX_REPLY_CONTEXT_PHOTOS - len(selected)
    if remaining and isinstance(quoted_candidate, dict):
        quoted_photos, quoted_expected = candidate_native_photo_media(
            quoted_candidate
        )
        quoted_required = min(quoted_expected, remaining)
        if len(quoted_photos) < quoted_required:
            metadata_complete = False
        seen_media = {str(photo.get("media_key") or "") for photo in selected}
        for photo in quoted_photos:
            media_key = str(photo.get("media_key") or "")
            if media_key in seen_media:
                continue
            selected.append(
                {
                    **photo,
                    "attachment_role": "quoted_subject",
                    "source_post_id": str(quoted_candidate.get("id") or ""),
                }
            )
            seen_media.add(media_key)
            if len(selected) >= MAX_REPLY_CONTEXT_PHOTOS:
                break

    expected_photo_count = target_expected + quoted_expected
    if selected and metadata_complete:
        log.info(
            "Reply media context lane=%s target_id=%s photos=%d mode=multimodal status=supplied",
            lane,
            target_id,
            len(selected),
        )
        return {
            "lane": lane,
            "target_id": str(target_id),
            "mode": "multimodal",
            "status": "supplied",
            "photos_expected": len(selected),
            "photos": selected,
        }

    if expected_photo_count:
        log.warning(
            "Reply media context unavailable lane=%s target_id=%s photos_expected=%d mode=multimodal status=unavailable",
            lane,
            target_id,
            expected_photo_count,
        )
        return {
            "lane": lane,
            "target_id": str(target_id),
            "mode": "multimodal",
            "status": "unavailable",
            "photos_expected": expected_photo_count,
            "photos": [],
        }

    return {
        "lane": lane,
        "target_id": str(target_id),
        "mode": "none",
        "status": "none",
        "photos_expected": 0,
        "photos": [],
    }

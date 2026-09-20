"""Own reply photo selection, media context and bounded image collection.

The root creates an inert owner with current caps, image policy, transport,
validators, exception classes and logging capabilities. Methods call their owned
selection, URL checks and ordered response validation directly. Fixed loopback
recognition comes from the route-value owner. Attachment expansion is pure;
state, delivery, provider generation and persistence retain their authorities.
Import performs no file, environment, provider or RNG work.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger
from urllib.parse import urlsplit

from mrs_bot_request_route_values import endpoint_is_loopback


_REPLY_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}


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


@dataclass(frozen=True)
class ReplyMedia:
    """Select and collect reply images using current external capabilities."""

    maximum_context_photos: int
    maximum_supplied_images: int
    maximum_image_bytes: int
    image_mime_types: set[str]
    log: Logger
    media_unavailable: type
    media_transient_unavailable: type
    test_mode: bool
    require_remote_operation_unpaused: Callable
    requests: object
    request_timeout: Callable
    validate_supplied_images: Callable

    def candidate_photos(self, candidate: dict) -> tuple[list[dict], int]:
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
            if len(usable) >= self.maximum_context_photos:
                break

        unresolved_count = len(
            unresolved_declared_keys | unclassified_declared_keys
        )
        if malformed_attachment_metadata:
            unresolved_count = max(1, unresolved_count)
        return usable, len(photo_records) + unresolved_count

    def context(
        self,
        candidate: dict,
        *,
        lane: str,
        target_id: str,
        quoted_candidate: dict | None = None,
    ) -> dict:
        """Prioritise target photos, then photos from the directly quoted post."""

        target_photos, target_expected = self.candidate_photos(candidate)
        target_required = min(target_expected, self.maximum_context_photos)
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
                for photo in target_photos[:self.maximum_context_photos]
            ]
            metadata_complete = True

        quoted_expected = 0
        quoted_photos: list[dict] = []
        remaining = self.maximum_context_photos - len(selected)
        if remaining and isinstance(quoted_candidate, dict):
            quoted_photos, quoted_expected = self.candidate_photos(
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
                if len(selected) >= self.maximum_context_photos:
                    break

        expected_photo_count = target_expected + quoted_expected
        if selected and metadata_complete:
            self.log.info(
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
            self.log.warning(
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

    def safe_url(self, value: object) -> str:
        """Validate the candidate image URL against trusted media origins."""
        url = str(value or "").strip()
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise self.media_unavailable("candidate image URL has an invalid port") from exc
        trusted_production_origin = bool(
            parsed.scheme == "https"
            and parsed.hostname == "pbs.twimg.com"
            and port in {None, 443}
        )
        trusted_test_origin = bool(
            self.test_mode
            and parsed.scheme == "http"
            and endpoint_is_loopback(url)
            and parsed.path.startswith("/media/")
        )
        if (
            not (trusted_production_origin or trusted_test_origin)
            or parsed.username is not None
            or parsed.password is not None
            or not parsed.path.startswith("/media/")
            or parsed.fragment
        ):
            raise self.media_unavailable("candidate image URL is outside the trusted X media origin")
        return url

    def collect(self, media_context: dict | None) -> list[dict[str, object]]:
        """Collect up to two already-identified native X images with hard bounds."""

        if not isinstance(media_context, dict):
            return []
        status = media_context.get("status")
        expected = int(media_context.get("photos_expected", 0) or 0)
        if status == "none" and expected == 0:
            return []
        photos = media_context.get("photos")
        required_count = min(expected, self.maximum_supplied_images)
        if (
            status != "supplied"
            or not isinstance(photos, list)
            or required_count < 1
            or len(photos) != required_count
        ):
            raise self.media_unavailable("material candidate image metadata is incomplete")
        collected: list[dict[str, object]] = []
        for index, photo in enumerate(photos, 1):
            if not isinstance(photo, dict):
                raise self.media_unavailable("candidate image metadata is invalid")
            identity = str(photo.get("media_key") or "").strip()
            if not identity:
                raise self.media_unavailable("candidate image lacks a stable identity")
            url = self.safe_url(photo.get("url"))
            mime_type, image_bytes = self._download_image(
                url, operation=f"candidate image collection {index}/{len(photos)}"
            )
            collected.append(
                {
                    "identity": identity,
                    "mime_type": mime_type,
                    "data": image_bytes,
                    "attachment_role": str(photo.get("attachment_role") or ""),
                    "source_post_id": str(photo.get("source_post_id") or ""),
                }
            )
        try:
            return self.validate_supplied_images(collected)
        except (RuntimeError, TypeError, ValueError) as exc:
            raise self.media_unavailable("candidate image bytes failed validation") from exc

    def _validated_response_headers(self, response: object) -> tuple[str, int | None]:
        """Reject unsafe response metadata before consuming image bytes."""
        if response.status_code != 200:
            failure_type = (
                self.media_transient_unavailable
                if response.status_code in {408, 425, 429}
                or 500 <= response.status_code < 600
                else self.media_unavailable
            )
            raise failure_type(
                f"candidate image returned HTTP {response.status_code}"
            )
        if str(response.headers.get("Content-Encoding") or "identity").lower() != "identity":
            raise self.media_unavailable("candidate image transfer encoding is unsupported")
        if response.headers.get("Location"):
            raise self.media_unavailable("candidate image attempted a redirect")
        mime_type = str(
            response.headers.get("Content-Type") or ""
        ).split(";", 1)[0].strip().lower()
        if mime_type not in self.image_mime_types:
            raise self.media_unavailable("candidate image type is unsupported")
        raw_length = response.headers.get("Content-Length")
        content_length = None
        if raw_length is not None:
            try:
                content_length = int(raw_length)
            except ValueError as exc:
                raise self.media_unavailable(
                    "candidate image length is invalid"
                ) from exc
            if not 1 <= content_length <= self.maximum_image_bytes:
                raise self.media_unavailable(
                    "candidate image length is outside the safe bound"
                )
        return mime_type, content_length

    def _download_image(self, url: str, *, operation: str) -> tuple[str, bytes]:
        """Read one bounded image and close its response before returning bytes."""

        self.require_remote_operation_unpaused(operation)
        response = None
        try:
            response = self.requests.get(
                url,
                stream=True,
                allow_redirects=False,
                timeout=self.request_timeout(),
                headers={"Accept": "image/jpeg,image/png,image/webp,image/gif", "Accept-Encoding": "identity"},
            )
            mime_type, content_length = self._validated_response_headers(response)
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not isinstance(chunk, bytes) or not chunk:
                    continue
                total += len(chunk)
                if total > self.maximum_image_bytes:
                    raise self.media_unavailable("candidate image exceeds the safe bound")
                chunks.append(chunk)
            if content_length is not None and total != content_length:
                failure_type = (self.media_transient_unavailable if total < content_length else self.media_unavailable)
                raise failure_type("candidate image body differs from declared length")
            image_bytes = b"".join(chunks)
        except self.media_unavailable:
            raise
        except self.requests.RequestException as exc:
            raise self.media_transient_unavailable(
                "candidate image could not be obtained safely"
            ) from exc
        finally:
            if response is not None:
                close_response = getattr(response, "close", None)
                if callable(close_response):
                    close_response()
        return mime_type, image_bytes

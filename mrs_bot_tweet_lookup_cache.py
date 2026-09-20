"""Own tweet lookup, cache normalization/pruning and the recent own-post index.

TweetLookupCache binds current normalization, request, clock, policy and persistence
boundaries for each root invocation without accessing runtime state. Owned lookup,
verification, normalization, pruning and storage call each other directly while
preserving permissive values, mutation/save order and record references.
Full text is selected before caching. Legacy external cache entries are refreshed
on use; verified media-only refreshes retain their existing copy boundaries.

Shared state epochs, request/authentication/error classification, media attachment,
configuration, persistence and orchestration remain in their existing locations.
Attachment expansion uses its inert owner directly. This module performs no
import-time file, environment, clock, provider or RNG work. Construction retains
no caller state.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger
from pathlib import Path

from mrs_bot_reply_native_media import attach_media_to_tweets


def normalise_tweet_text(tweet: dict) -> None:
    """Select X's complete long-post text and entities before any reply processing."""
    note = tweet.get("note_tweet")
    if isinstance(note, dict) and isinstance(note.get("text"), str) and note["text"].strip():
        tweet["text"] = note["text"]
        if isinstance(note.get("entities"), dict):
            # X's ordinary entities may include implicit reply recipients which
            # are absent from the long-post body. Retain that eligibility evidence.
            ordinary = tweet.get("entities")
            entities = dict(note["entities"])
            mentions = list(entities["mentions"]) if isinstance(entities.get("mentions"), list) else []
            if isinstance(ordinary, dict) and isinstance(ordinary.get("mentions"), list):
                mentions.extend(item for item in ordinary["mentions"] if item not in mentions)
            if mentions:
                entities["mentions"] = mentions
            tweet["entities"] = entities
    tweet["text_is_complete"] = True


def tweet_text_is_complete(tweet: dict) -> bool:
    """Accept verified X text or locally authored posts from the legacy cache."""
    return tweet.get("text_is_complete") is True or tweet.get("post_type") in {
        "quote", "daily_meme", "auto_reply",
    }


@dataclass(frozen=True)
class TweetLookupCache:
    """Own verified lookup, cached context and the recent own-post index."""

    normalise_state_epoch: Callable
    maximum_age_seconds: int
    maximum_items: int
    log: Logger
    now_epoch: Callable
    maximum_recent_own_posts: int
    user_id: str
    state_file: Path
    current_datetime: Callable
    api_error: type[Exception]
    log_json_debug: Callable
    request: Callable
    is_permanent_target_failure: Callable
    save_state: Callable

    def normalise_entry(self, tweet_id: object, entry: dict, *, path: Path) -> dict[str, object] | None:
        """Normalise tweet cache entry."""
        cached_epoch = self.normalise_state_epoch(entry.get("cached_epoch", 0), key=f"tweet_cache.{tweet_id}.cached_epoch", path=path)
        if cached_epoch is None:
            return None

        referenced_tweets = entry.get("referenced_tweets", [])
        if not isinstance(referenced_tweets, list):
            self.log.error(
                "State candidate %s has invalid tweet_cache.%s.referenced_tweets type %s; ignoring",
                path,
                tweet_id,
                type(referenced_tweets).__name__,
            )
            return None
        normalized_refs: list[dict[str, str]] = []
        for index, ref in enumerate(referenced_tweets):
            if not isinstance(ref, dict):
                self.log.error(
                    "State candidate %s has invalid tweet_cache.%s.referenced_tweets[%d] type %s; ignoring",
                    path,
                    tweet_id,
                    index,
                    type(ref).__name__,
                )
                return None
            normalized_ref: dict[str, str] = {}
            if ref.get("type") is not None:
                normalized_ref["type"] = str(ref.get("type"))
            if ref.get("id") is not None:
                normalized_ref["id"] = str(ref.get("id"))
            for key, ref_value in ref.items():
                if key in {"type", "id"} or ref_value is None:
                    continue
                normalized_ref[str(key)] = str(ref_value)
            normalized_refs.append(normalized_ref)

        entry_id = entry.get("id")
        normalized_id = str(entry_id if entry_id is not None else tweet_id)
        conversation_id = entry.get("conversation_id")
        normalized_entry: dict[str, object] = {
            "id": normalized_id,
            "author_id": str(entry.get("author_id") or ""),
            "conversation_id": str(conversation_id if conversation_id is not None else normalized_id),
            "created_at": str(entry.get("created_at") or ""),
            "referenced_tweets": normalized_refs,
            "text": str(entry.get("text") or ""),
            "cached_epoch": cached_epoch,
        }
        if entry.get("text_is_complete") is True:
            normalized_entry["text_is_complete"] = True
        if entry.get("image_summary") is not None:
            normalized_entry["image_summary"] = str(entry.get("image_summary"))
        if entry.get("post_type") is not None:
            normalized_entry["post_type"] = str(entry.get("post_type"))
        return normalized_entry

    def normalise(self, value: object, *, path: Path) -> dict[str, dict] | None:
        """Normalise tweet cache."""
        if not isinstance(value, dict):
            self.log.error("State candidate %s has invalid tweet_cache type %s; ignoring", path, type(value).__name__)
            return None
        out: dict[str, dict] = {}
        for tweet_id, entry in value.items():
            if not isinstance(entry, dict):
                self.log.error(
                    "State candidate %s has invalid tweet_cache.%s type %s; ignoring",
                    path,
                    tweet_id,
                    type(entry).__name__,
                )
                return None
            normalized_entry = self.normalise_entry(tweet_id, entry, path=path)
            if normalized_entry is None:
                return None
            out[str(tweet_id)] = normalized_entry
        return out

    def prune(self, state: dict) -> None:
        """Prune tweet cache."""
        cache = state.setdefault("tweet_cache", {})
        cutoff = self.now_epoch() - self.maximum_age_seconds

        pruned = {
            str(tweet_id): tweet
            for tweet_id, tweet in cache.items()
            if int(tweet.get("cached_epoch", 0) or 0) >= cutoff
        }

        if len(pruned) > self.maximum_items:
            items = sorted(
                pruned.items(),
                key=lambda kv: int(kv[1].get("cached_epoch", 0) or 0),
                reverse=True,
            )
            pruned = dict(items[:self.maximum_items])

        if len(pruned) != len(cache):
            self.log.info("Pruned tweet cache from %d to %d items", len(cache), len(pruned))

        state["tweet_cache"] = pruned

    def record_recent_own_post(self, state: dict, tweet_id: str) -> None:
        """Record recent own post."""
        tweet_id = str(tweet_id)

        ids = [str(x) for x in state.get("recent_own_post_ids", []) if str(x) != tweet_id]
        ids.insert(0, tweet_id)
        state["recent_own_post_ids"] = ids[:self.maximum_recent_own_posts]

        self.log.info("Recorded recent own post id=%s recent_count=%d", tweet_id, len(state["recent_own_post_ids"]))

    def seed_recent_own_posts(self, state: dict) -> None:
        """Seed recent own post IDs from cache."""
        if state.get("recent_own_post_ids"):
            return

        cache = state.get("tweet_cache", {})
        own_posts: list[tuple[int, str]] = []

        for tweet_id, tweet in cache.items():
            if str(tweet.get("author_id")) != str(self.user_id):
                continue

            post_type = tweet.get("post_type")
            if post_type not in {"quote", "daily_meme"}:
                continue

            own_posts.append((int(tweet.get("cached_epoch", 0) or 0), str(tweet_id)))

        own_posts.sort(reverse=True)
        seeded = [tweet_id for _, tweet_id in own_posts[:self.maximum_recent_own_posts]]

        if not seeded and state.get("last_main_post_id"):
            seeded = [str(state["last_main_post_id"])]

        state["recent_own_post_ids"] = seeded
        self.log.info("Seeded recent_own_post_ids from cache count=%d", len(seeded))

    def store(
        self, state: dict, *, tweet_id: str, text: str, author_id: str,
        conversation_id: str | None = None,
        referenced_tweets: list[dict] | None = None,
        created_at: str | None = None, image_summary: str | None = None,
        post_type: str | None = None,
    ) -> dict:
        """Cache complete provider-normalized or locally authored post text."""
        self.prune(state)

        tweet_id = str(tweet_id)
        cache = state.setdefault("tweet_cache", {})

        cached_tweet = {
            "id": tweet_id,
            "author_id": str(author_id),
            "conversation_id": str(conversation_id or tweet_id),
            "created_at": created_at or self.current_datetime().isoformat(),
            "referenced_tweets": referenced_tweets or [],
            "text": text or "",
            "cached_epoch": self.now_epoch(),
            "text_is_complete": True,
        }

        if image_summary:
            cached_tweet["image_summary"] = image_summary

        if post_type:
            cached_tweet["post_type"] = post_type

        normalised_tweet = self.normalise_entry(tweet_id, cached_tweet, path=self.state_file)
        if normalised_tweet is None:
            raise ValueError(f"Refusing to cache malformed tweet entry id={tweet_id}")

        cached_tweet = normalised_tweet
        cache[tweet_id] = cached_tweet
        state["tweet_cache"] = cache

        self.log.info(
            "Cached tweet id=%s author_id=%s cache_size=%d post_type=%s image_summary=%s",
            tweet_id,
            author_id,
            len(cache),
            post_type or "",
            bool(image_summary),
        )

        return cached_tweet

    def verified_row(self, tweet: object, *, requested_tweet_id: str) -> dict:
        """Return only a lookup row bound to the exact requested post identity."""

        request_id = str(requested_tweet_id)
        if not isinstance(tweet, dict):
            raise self.api_error(
                "X tweet lookup returned missing or malformed tweet data",
                service="x",
                request_method="GET",
                request_path=f"/2/tweets/{request_id}",
            )
        if str(tweet.get("id") or "") != request_id:
            raise self.api_error(
                "X tweet lookup returned a mismatched post",
                service="x",
                request_method="GET",
                request_path=f"/2/tweets/{request_id}",
            )
        return tweet

    def fetch(self, tweet_id: str, *, include_media: bool = False) -> dict | None:
        """Fetch one post from X by ID, optionally including native image metadata."""
        self.log.info("Fetching tweet by id. tweet_id=%s", tweet_id)

        tweet_fields = "author_id,created_at,conversation_id,referenced_tweets,entities,note_tweet"
        params = {"tweet.fields": tweet_fields}
        if include_media:
            params.update(
                {
                    "tweet.fields": f"{tweet_fields},attachments",
                    "expansions": "attachments.media_keys",
                    "media.fields": "media_key,type,url,preview_image_url",
                }
            )

        result = self.request(
            "GET",
            f"/2/tweets/{tweet_id}",
            params=params,
        )

        tweet = self.verified_row(
            result.get("data") if isinstance(result, dict) else None,
            requested_tweet_id=str(tweet_id),
        )
        if isinstance(tweet, dict):
            normalise_tweet_text(tweet)
        if include_media and isinstance(tweet, dict):
            attach_media_to_tweets([tweet], result.get("includes"))
        self.log_json_debug("Fetched tweet", tweet)

        return tweet

    def target_is_available(self, target_id: str) -> bool:
        """Freshly prove that one reply target still exists before durable send setup.

        Reply generation can take long enough for a fetched mention or quote tweet
        to be deleted in the meantime.  A direct lookup here deliberately bypasses
        the tweet cache and runs before the sending receipt and transport journal
        are created.  Target-specific lookup failures are terminal for this
        candidate; authentication, endpoint and transient failures remain errors
        so callers retain the validated draft for a later attempt.
        """

        target_id = str(target_id)
        self.log.info(
            "Freshly revalidating reply target immediately before send. target_id=%s",
            target_id,
        )
        try:
            target = self.fetch(target_id)
        except self.api_error as exc:
            if self.is_permanent_target_failure(exc):
                self.log.warning(
                    "Reply target became unavailable before send. target_id=%s",
                    target_id,
                )
                return False
            raise

        if not isinstance(target, dict) or str(target.get("id") or "") != target_id:
            raise self.api_error(
                "X reply-target pre-send lookup returned a missing, mismatched or malformed post",
                service="x",
                request_method="GET",
                request_path=f"/2/tweets/{target_id}",
            )

        self.log.info("Fresh pre-send reply-target lookup passed. target_id=%s", target_id)
        return True

    def get_cached(self, tweet_id: str, state: dict, *, include_media: bool = False) -> dict | None:
        """Return complete cached text, refreshing legacy external text or requested media."""
        self.prune(state)

        tweet_id = str(tweet_id)
        cache = state.setdefault("tweet_cache", {})
        cached = cache.get(tweet_id)
        if cached is not None:
            cached = self.verified_row(
                cached,
                requested_tweet_id=tweet_id,
            )

        needs_text_refresh = bool(cached and not tweet_text_is_complete(cached))
        if cached and not include_media and not needs_text_refresh:
            self.log.info("Using cached tweet for context. tweet_id=%s", tweet_id)
            return cached

        tweet = (
            self.fetch(tweet_id, include_media=True)
            if include_media
            else self.fetch(tweet_id)
        )

        if tweet:
            tweet = self.verified_row(tweet, requested_tweet_id=tweet_id)
            normalise_tweet_text(tweet)
            if cached:
                result = copy.deepcopy(cached)
                for key in (
                    "id",
                    "text",
                    "author_id",
                    "conversation_id",
                    "referenced_tweets",
                    "created_at",
                    "attachments",
                    "_attached_media",
                    "entities",
                    "text_is_complete",
                ):
                    if key in tweet:
                        result[key] = copy.deepcopy(tweet[key])
                if needs_text_refresh:
                    # Persist only canonical text metadata; retain local image/type
                    # context and leave transient media attachments on the return.
                    cached.update({key: copy.deepcopy(result[key]) for key in (
                        "text", "author_id", "conversation_id", "referenced_tweets", "created_at"
                    ) if key in result})
                    cached["text_is_complete"] = True
                    result["text_is_complete"] = True
                    self.save_state(state)
                return result
            cached_tweet = self.store(
                state,
                tweet_id=str(tweet.get("id", tweet_id)),
                text=tweet.get("text", ""),
                author_id=str(tweet.get("author_id", "")),
                conversation_id=str(tweet.get("conversation_id", tweet_id)),
                referenced_tweets=tweet.get("referenced_tweets", []),
                created_at=tweet.get("created_at"),
            )
            self.save_state(state)
            result = copy.deepcopy(cached_tweet)
            if "attachments" in tweet:
                result["attachments"] = copy.deepcopy(tweet["attachments"])
            if "_attached_media" in tweet:
                result["_attached_media"] = copy.deepcopy(tweet["_attached_media"])
            return result

        return None

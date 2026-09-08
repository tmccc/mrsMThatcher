"""Own tweet lookup, cache normalization/pruning and the recent own-post index.

Ten root adapters supply current callbacks, API exception, settings, clock,
logger, path and copy-module authority on every call. Original bodies preserve
permissive normalization, pruning/mutation/save order and record references.
Full text is selected before caching. Legacy external cache entries are refreshed
on use; verified media-only refreshes retain their existing copy boundaries.

Shared state epochs, request/authentication/error classification, media attachment,
configuration, persistence and orchestration remain in their existing locations.
This standard-library-only owner retains no callbacks, configuration, clients or
state and performs no import-time file, environment, clock, provider or RNG work.
"""

from __future__ import annotations

from collections.abc import Callable
from logging import Logger
from pathlib import Path
from types import ModuleType


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


def normalise_tweet_cache_entry(
    tweet_id: object,
    entry: dict,
    *,
    path: Path,
    log: Logger,
    normalise_state_epoch: Callable,
) -> dict[str, object] | None:
    """Normalise tweet cache entry."""
    cached_epoch = normalise_state_epoch(entry.get("cached_epoch", 0), key=f"tweet_cache.{tweet_id}.cached_epoch", path=path)
    if cached_epoch is None:
        return None

    referenced_tweets = entry.get("referenced_tweets", [])
    if not isinstance(referenced_tweets, list):
        log.error(
            "State candidate %s has invalid tweet_cache.%s.referenced_tweets type %s; ignoring",
            path,
            tweet_id,
            type(referenced_tweets).__name__,
        )
        return None
    normalized_refs: list[dict[str, str]] = []
    for index, ref in enumerate(referenced_tweets):
        if not isinstance(ref, dict):
            log.error(
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


def normalise_tweet_cache(
    value: object,
    *,
    path: Path,
    log: Logger,
    normalise_tweet_cache_entry: Callable,
) -> dict[str, dict] | None:
    """Normalise tweet cache."""
    if not isinstance(value, dict):
        log.error("State candidate %s has invalid tweet_cache type %s; ignoring", path, type(value).__name__)
        return None
    out: dict[str, dict] = {}
    for tweet_id, entry in value.items():
        if not isinstance(entry, dict):
            log.error(
                "State candidate %s has invalid tweet_cache.%s type %s; ignoring",
                path,
                tweet_id,
                type(entry).__name__,
            )
            return None
        normalized_entry = normalise_tweet_cache_entry(tweet_id, entry, path=path)
        if normalized_entry is None:
            return None
        out[str(tweet_id)] = normalized_entry
    return out


def prune_tweet_cache(
    state: dict,
    *,
    TWEET_CACHE_MAX_AGE_SECONDS: int,
    TWEET_CACHE_MAX_ITEMS: int,
    log: Logger,
    now_epoch: Callable,
) -> None:
    """Prune tweet cache."""
    cache = state.setdefault("tweet_cache", {})
    cutoff = now_epoch() - TWEET_CACHE_MAX_AGE_SECONDS

    pruned = {
        str(tweet_id): tweet
        for tweet_id, tweet in cache.items()
        if int(tweet.get("cached_epoch", 0) or 0) >= cutoff
    }

    if len(pruned) > TWEET_CACHE_MAX_ITEMS:
        items = sorted(
            pruned.items(),
            key=lambda kv: int(kv[1].get("cached_epoch", 0) or 0),
            reverse=True,
        )
        pruned = dict(items[:TWEET_CACHE_MAX_ITEMS])

    if len(pruned) != len(cache):
        log.info("Pruned tweet cache from %d to %d items", len(cache), len(pruned))

    state["tweet_cache"] = pruned


def record_recent_own_post(
    state: dict,
    tweet_id: str,
    *,
    RECENT_OWN_POST_IDS_MAX: int,
    log: Logger,
) -> None:
    """Record recent own post."""
    tweet_id = str(tweet_id)

    ids = [str(x) for x in state.get("recent_own_post_ids", []) if str(x) != tweet_id]
    ids.insert(0, tweet_id)
    state["recent_own_post_ids"] = ids[:RECENT_OWN_POST_IDS_MAX]

    log.info("Recorded recent own post id=%s recent_count=%d", tweet_id, len(state["recent_own_post_ids"]))


def seed_recent_own_post_ids_from_cache(
    state: dict,
    *,
    MY_USER_ID: str,
    RECENT_OWN_POST_IDS_MAX: int,
    log: Logger,
) -> None:
    """Seed recent own post IDs from cache."""
    if state.get("recent_own_post_ids"):
        return

    cache = state.get("tweet_cache", {})
    own_posts: list[tuple[int, str]] = []

    for tweet_id, tweet in cache.items():
        if str(tweet.get("author_id")) != str(MY_USER_ID):
            continue

        post_type = tweet.get("post_type")
        if post_type not in {"quote", "daily_meme"}:
            continue

        own_posts.append((int(tweet.get("cached_epoch", 0) or 0), str(tweet_id)))

    own_posts.sort(reverse=True)
    seeded = [tweet_id for _, tweet_id in own_posts[:RECENT_OWN_POST_IDS_MAX]]

    if not seeded and state.get("last_main_post_id"):
        seeded = [str(state["last_main_post_id"])]

    state["recent_own_post_ids"] = seeded
    log.info("Seeded recent_own_post_ids from cache count=%d", len(seeded))


def cache_tweet(
    state: dict,
    *,
    tweet_id: str,
    text: str,
    author_id: str,
    conversation_id: str | None = None,
    referenced_tweets: list[dict] | None = None,
    created_at: str | None = None,
    image_summary: str | None = None,
    post_type: str | None = None,
    STATE_FILE: Path,
    current_datetime: Callable,
    log: Logger,
    normalise_tweet_cache_entry: Callable,
    now_epoch: Callable,
    prune_tweet_cache: Callable,
) -> dict:
    """Cache complete provider-normalized or locally authored post text."""
    prune_tweet_cache(state)

    tweet_id = str(tweet_id)
    cache = state.setdefault("tweet_cache", {})

    cached_tweet = {
        "id": tweet_id,
        "author_id": str(author_id),
        "conversation_id": str(conversation_id or tweet_id),
        "created_at": created_at or current_datetime().isoformat(),
        "referenced_tweets": referenced_tweets or [],
        "text": text or "",
        "cached_epoch": now_epoch(),
        "text_is_complete": True,
    }

    if image_summary:
        cached_tweet["image_summary"] = image_summary

    if post_type:
        cached_tweet["post_type"] = post_type

    normalised_tweet = normalise_tweet_cache_entry(tweet_id, cached_tweet, path=STATE_FILE)
    if normalised_tweet is None:
        raise ValueError(f"Refusing to cache malformed tweet entry id={tweet_id}")

    cached_tweet = normalised_tweet
    cache[tweet_id] = cached_tweet
    state["tweet_cache"] = cache

    log.info(
        "Cached tweet id=%s author_id=%s cache_size=%d post_type=%s image_summary=%s",
        tweet_id,
        author_id,
        len(cache),
        post_type or "",
        bool(image_summary),
    )

    return cached_tweet


def _verified_tweet_lookup_row(
    tweet: object,
    *,
    requested_tweet_id: str,
    ApiError: type[Exception],
) -> dict | None:
    """Return only a lookup row bound to the exact requested post identity."""

    if tweet is None:
        return None
    request_id = str(requested_tweet_id)
    if not isinstance(tweet, dict):
        raise ApiError(
            "X tweet lookup returned malformed tweet data",
            service="x",
            request_method="GET",
            request_path=f"/2/tweets/{request_id}",
        )
    if str(tweet.get("id") or "") != request_id:
        raise ApiError(
            "X tweet lookup returned a mismatched post",
            service="x",
            request_method="GET",
            request_path=f"/2/tweets/{request_id}",
        )
    return tweet


def get_tweet_by_id(
    tweet_id: str,
    *,
    include_media: bool = False,
    _verified_tweet_lookup_row: Callable,
    attach_media_to_tweets: Callable,
    log: Logger,
    log_json_debug: Callable,
    x_request: Callable,
) -> dict | None:
    """Fetch one post from X by ID, optionally including native image metadata."""
    log.info("Fetching tweet by id. tweet_id=%s", tweet_id)

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

    result = x_request(
        "GET",
        f"/2/tweets/{tweet_id}",
        params=params,
    )

    tweet = _verified_tweet_lookup_row(
        result.get("data"),
        requested_tweet_id=str(tweet_id),
    )
    if isinstance(tweet, dict):
        normalise_tweet_text(tweet)
    if include_media and isinstance(tweet, dict):
        attach_media_to_tweets([tweet], result.get("includes"))
    log_json_debug("Fetched tweet", tweet)

    return tweet


def reply_target_is_available_immediately_before_send(
    target_id: str,
    *,
    ApiError: type[Exception],
    api_error_is_permanent_target_failure: Callable,
    get_tweet_by_id: Callable,
    log: Logger,
) -> bool:
    """Freshly prove that one reply target still exists before durable send setup.

    Reply generation can take long enough for a fetched mention or quote tweet
    to be deleted in the meantime.  A direct lookup here deliberately bypasses
    the tweet cache and runs before the sending receipt and transport journal
    are created.  Target-specific lookup failures are terminal for this
    candidate; authentication, endpoint and transient failures remain errors
    so callers retain the validated draft for a later attempt.
    """

    target_id = str(target_id)
    log.info(
        "Freshly revalidating reply target immediately before send. target_id=%s",
        target_id,
    )
    try:
        target = get_tweet_by_id(target_id)
    except ApiError as exc:
        if api_error_is_permanent_target_failure(exc):
            log.warning(
                "Reply target became unavailable before send. target_id=%s",
                target_id,
            )
            return False
        raise

    if target is None:
        log.warning(
            "Reply target was absent in the fresh pre-send lookup. target_id=%s",
            target_id,
        )
        return False
    if not isinstance(target, dict) or str(target.get("id") or "") != target_id:
        raise ApiError(
            "X reply-target pre-send lookup returned a mismatched or malformed post",
            service="x",
            request_method="GET",
            request_path=f"/2/tweets/{target_id}",
        )

    log.info("Fresh pre-send reply-target lookup passed. target_id=%s", target_id)
    return True


def get_tweet_by_id_cached(
    tweet_id: str,
    state: dict,
    *,
    include_media: bool = False,
    _verified_tweet_lookup_row: Callable,
    cache_tweet: Callable,
    copy: ModuleType,
    get_tweet_by_id: Callable,
    log: Logger,
    prune_tweet_cache: Callable,
    save_state: Callable,
) -> dict | None:
    """Return complete cached text, refreshing legacy external text or requested media."""
    prune_tweet_cache(state)

    tweet_id = str(tweet_id)
    cache = state.setdefault("tweet_cache", {})
    cached = cache.get(tweet_id)
    if cached is not None:
        cached = _verified_tweet_lookup_row(
            cached,
            requested_tweet_id=tweet_id,
        )

    needs_text_refresh = bool(cached and not tweet_text_is_complete(cached))
    if cached and not include_media and not needs_text_refresh:
        log.info("Using cached tweet for context. tweet_id=%s", tweet_id)
        return cached

    tweet = (
        get_tweet_by_id(tweet_id, include_media=True)
        if include_media
        else get_tweet_by_id(tweet_id)
    )

    if tweet:
        tweet = _verified_tweet_lookup_row(tweet, requested_tweet_id=tweet_id)
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
                save_state(state)
            return result
        cached_tweet = cache_tweet(
            state,
            tweet_id=str(tweet.get("id", tweet_id)),
            text=tweet.get("text", ""),
            author_id=str(tweet.get("author_id", "")),
            conversation_id=str(tweet.get("conversation_id", tweet_id)),
            referenced_tweets=tweet.get("referenced_tweets", []),
            created_at=tweet.get("created_at"),
        )
        save_state(state)
        result = copy.deepcopy(cached_tweet)
        if "attachments" in tweet:
            result["attachments"] = copy.deepcopy(tweet["attachments"])
        if "_attached_media" in tweet:
            result["_attached_media"] = copy.deepcopy(tweet["_attached_media"])
        return result

    return None

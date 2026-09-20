"""Own watched own-post selection and quote discovery/pagination.

QuoteWatchPosts owns fresh watch-file reads, recent-original selection and their
priority merge. Its methods call each other directly with current root settings
and cache seeding. Discovery adapters supply current request, state and runtime
boundaries. Recent search batches watched originals and groups direct quotes;
the legacy per-post lookup remains a diagnostic helper.

Shared pagination, request/authentication, ID validation, cache seeding, media,
durable persistence and reply cycles remain in their existing locations. This
owner acquires no write authority and retains no caller state. Import performs
no file, environment, provider, clock or RNG work or reverse application import.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger
from pathlib import Path

from mrs_bot_reply_native_media import attach_media_to_tweets
from mrs_bot_state_value_normalisation import bounded_tweet_id_value
from mrs_bot_tweet_lookup_cache import normalise_tweet_text


def quote_repeated_cursor_suppression_record(
    post_id: object,
    value: object,
    *,
    current_epoch: int,
    allow_expired: bool = False,
    MAX_REASONABLE_STATE_EPOCH: int,
    QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS: int,
) -> dict[str, object] | None:
    """Return one canonical exact-cursor suppression record when usable."""
    if (
        type(post_id) is not str
        or bounded_tweet_id_value(post_id) is None
        or not isinstance(value, dict)
        or set(value)
        != {"cursor_sha256", "detected_epoch", "retry_after_epoch"}
    ):
        return None
    cursor_sha256 = value.get("cursor_sha256")
    detected_epoch = value.get("detected_epoch")
    retry_after_epoch = value.get("retry_after_epoch")
    if (
        type(cursor_sha256) is not str
        or re.fullmatch(r"[0-9a-f]{64}", cursor_sha256) is None
        or type(detected_epoch) is not int
        or detected_epoch < 0
        or detected_epoch > current_epoch
        or detected_epoch > MAX_REASONABLE_STATE_EPOCH
        or type(retry_after_epoch) is not int
        or retry_after_epoch
        != detected_epoch + QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS
        or retry_after_epoch > MAX_REASONABLE_STATE_EPOCH
        or (not allow_expired and retry_after_epoch <= current_epoch)
    ):
        return None
    return {
        "cursor_sha256": cursor_sha256,
        "detected_epoch": detected_epoch,
        "retry_after_epoch": retry_after_epoch,
    }


def normalise_quote_repeated_cursor_suppressions(
    value: object,
    *,
    current_epoch: int | None = None,
    QUOTE_REPEATED_CURSOR_SUPPRESSION_MAX_ENTRIES: int,
    now_epoch: Callable,
    quote_repeated_cursor_suppression_record: Callable,
) -> tuple[dict[str, dict[str, object]], int]:
    """Discard malformed, expired, and excess quote-cursor suppressions."""
    if current_epoch is None:
        current_epoch = now_epoch()
    if not isinstance(value, dict):
        return {}, 1

    retained: list[tuple[str, dict[str, object]]] = []
    for post_id, record in value.items():
        canonical = quote_repeated_cursor_suppression_record(
            post_id,
            record,
            current_epoch=current_epoch,
        )
        if canonical is not None:
            retained.append((post_id, canonical))

    retained.sort(
        key=lambda item: (
            int(item[1]["detected_epoch"]),
            int(item[1]["retry_after_epoch"]),
            item[0],
        ),
        reverse=True,
    )
    retained = retained[:QUOTE_REPEATED_CURSOR_SUPPRESSION_MAX_ENTRIES]
    normalised = dict(retained)
    discarded = max(0, len(value) - len(normalised))
    if normalised != value and discarded == 0:
        discarded = 1
    return normalised, discarded


@dataclass(frozen=True)
class QuoteWatchPosts:
    """Select watched and recent originals with fresh file reads and caller state."""

    watch_file: Path
    maximum_extra_posts: int
    maximum_posts: int
    lookback_posts: int
    seed_recent: Callable
    log: Logger

    def load_extra(self) -> list[str]:
        """
        Read extra own-post IDs to include in quote-tweet checks.

        This is deliberately read immediately before each quote-tweet check,
        not just at startup, so the file can be edited while the bot is running.

        File format:
          - one post ID per line
          - blank lines ignored
          - lines beginning with # ignored
          - inline comments allowed after whitespace + #
        """
        path = self.watch_file

        if not path.exists():
            return []

        post_ids: list[str] = []

        try:
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()

                if not line or line.startswith("#"):
                    continue

                # Allow:
                # 2070843320310419627  # hot meme post
                if " #" in line:
                    line = line.split(" #", 1)[0].strip()

                if not line.isdigit():
                    self.log.warning("Ignoring invalid extra quote-watch post ID in %s: %r", path, raw_line)
                    continue

                if line not in post_ids:
                    post_ids.append(line)

                if len(post_ids) >= self.maximum_extra_posts:
                    break

        except Exception as exc:
            self.log.warning("Could not read extra quote-watch post IDs from %s: %s", path, exc)
            return []

        return post_ids

    def lookup(self, state: dict) -> list[str]:
        """
        Build the list of own posts to inspect for quote-tweets.

        Extra watched posts are loaded fresh for every quote-tweet check and
        are given priority, so a hot older meme does not fall behind the last
        five ordinary quote/image posts.
        """
        recent_ids = self.recent(state)
        extra_ids = self.load_extra()

        clean_ids: list[str] = []
        seen: set[str] = set()

        # Extra watched posts first: these are explicitly selected hot posts.
        for tweet_id in extra_ids + recent_ids:
            tweet_id = str(tweet_id).strip()
            if not tweet_id or tweet_id in seen:
                continue
            seen.add(tweet_id)
            clean_ids.append(tweet_id)

            if len(clean_ids) >= self.maximum_posts:
                break

        if extra_ids:
            self.log.info(
                "Quote-tweet check loaded %d extra watched post(s) from %s: %s",
                len(extra_ids),
                self.watch_file,
                ", ".join(extra_ids),
            )

        self.log.info("Own posts for quote lookup: %s", clean_ids)

        return clean_ids

    def recent(self, state: dict) -> list[str]:
        """Return recent own post IDs for quote lookup."""
        self.seed_recent(state)

        ids = [str(x) for x in state.get("recent_own_post_ids", [])]

        if state.get("last_main_post_id"):
            last_id = str(state["last_main_post_id"])
            if last_id not in ids:
                ids.insert(0, last_id)

        clean_ids: list[str] = []
        seen: set[str] = set()

        for tweet_id in ids:
            if tweet_id in seen:
                continue
            seen.add(tweet_id)
            clean_ids.append(tweet_id)

        return clean_ids[:self.lookback_posts]


def get_quote_tweets_for_post(
    post_id: str,
    state: dict | None = None,
    *,
    QUOTE_LOOKUP_API_MAX_RESULTS: int,
    QUOTE_LOOKUP_MAX_PAGES_PER_POST: int,
    QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS: int,
    log: Logger,
    log_event: Callable,
    log_json_debug: Callable,
    normalise_quote_repeated_cursor_suppressions: Callable,
    now_epoch: Callable,
    quote_repeated_cursor_suppression_record: Callable,
    save_state: Callable,
    x_paginated_get: Callable,
    x_quote_lookup_request: Callable,
) -> list[dict]:
    """Read the legacy quote endpoint for diagnostics; the reply cycle uses search."""
    post_id = str(post_id)
    log.info("Fetching quote tweets for post_id=%s", post_id)

    params = {
        "max_results": QUOTE_LOOKUP_API_MAX_RESULTS,
        "tweet.fields": "author_id,created_at,conversation_id,referenced_tweets,attachments,entities,note_tweet",
        "expansions": "author_id,attachments.media_keys",
        "user.fields": "description,username,name,public_metrics",
        "media.fields": "media_key,type,url,preview_image_url",
    }
    pagination_tokens: dict[str, str] = {}
    suppressions: dict[str, dict[str, object]] = {}
    current_epoch = now_epoch()
    expired_probe_sha256 = ""
    suppression_state_changed = False
    if state is not None:
        raw_tokens = state.get("quote_lookup_pagination_tokens", {})
        if isinstance(raw_tokens, dict):
            pagination_tokens = {str(key): str(value) for key, value in raw_tokens.items() if str(value)}

        raw_suppressions = state.get(
            "quote_lookup_repeated_cursor_suppressions",
            {},
        )
        if isinstance(raw_suppressions, dict):
            expired_probe = quote_repeated_cursor_suppression_record(
                post_id,
                raw_suppressions.get(post_id),
                current_epoch=current_epoch,
                allow_expired=True,
            )
            if (
                expired_probe is not None
                and int(expired_probe["retry_after_epoch"]) <= current_epoch
            ):
                expired_probe_sha256 = str(expired_probe["cursor_sha256"])
        suppressions, discarded_suppressions = (
            normalise_quote_repeated_cursor_suppressions(
                raw_suppressions,
                current_epoch=current_epoch,
            )
        )
        if discarded_suppressions:
            state["quote_lookup_repeated_cursor_suppressions"] = dict(
                suppressions
            )
            suppression_state_changed = True
            log.debug(
                "Pruned %d malformed, expired, or excess quote cursor "
                "suppression entry or entries",
                discarded_suppressions,
            )

    active_suppression = suppressions.get(post_id)
    active_suppression_sha256 = (
        str(active_suppression.get("cursor_sha256") or "")
        if isinstance(active_suppression, dict)
        else ""
    )
    known_suppression_sha256 = (
        active_suppression_sha256 or expired_probe_sha256
    )
    suppression_is_active = bool(active_suppression_sha256)

    saved_pagination_token = pagination_tokens.get(post_id, "")
    if (
        saved_pagination_token
        and known_suppression_sha256
        and hashlib.sha256(saved_pagination_token.encode("utf-8")).hexdigest()
        == known_suppression_sha256
    ):
        pagination_tokens.pop(post_id, None)
        if state is not None:
            state["quote_lookup_pagination_tokens"] = dict(pagination_tokens)
        suppression_state_changed = True
        saved_pagination_token = ""
        log.debug(
            "Removed ordinary quote continuation matching a repeated-cursor "
            "suppression for post_id=%s token_fingerprint=%s",
            post_id,
            known_suppression_sha256[:16],
        )

    if state is not None and suppression_state_changed:
        save_state(state, durable=True)

    if saved_pagination_token:
        params["pagination_token"] = saved_pagination_token
        log.info(
            "Quote lookup for post_id=%s resuming with pagination_token_fingerprint=%s",
            post_id,
            hashlib.sha256(saved_pagination_token.encode("utf-8")).hexdigest()[:16],
        )

    def clear_invalid_quote_lookup_cursor() -> None:
        if state is None or post_id not in pagination_tokens:
            return
        pagination_tokens.pop(post_id, None)
        state["quote_lookup_pagination_tokens"] = dict(pagination_tokens)
        save_state(state, durable=True)

    def clear_known_repeated_cursor_suppression(reason: str) -> None:
        nonlocal known_suppression_sha256, suppression_is_active
        if not known_suppression_sha256:
            return
        token_fingerprint = known_suppression_sha256[:16]
        existing = suppressions.get(post_id)
        if (
            state is not None
            and isinstance(existing, dict)
            and existing.get("cursor_sha256") == known_suppression_sha256
        ):
            suppressions.pop(post_id, None)
            state["quote_lookup_repeated_cursor_suppressions"] = dict(
                suppressions
            )
            save_state(state, durable=True)
        known_suppression_sha256 = ""
        suppression_is_active = False
        log.info(
            "Quote pagination cursor suppression cleared post_id=%s "
            "token_fingerprint=%s reason=%s",
            post_id,
            token_fingerprint,
            reason,
        )

    def observe_quote_lookup_page(
        _page_data: list[dict],
        _includes: dict,
        next_token: str,
        _request_token: str,
        _pages_fetched: int,
    ) -> None:
        if not known_suppression_sha256:
            return
        if not next_token:
            clear_known_repeated_cursor_suppression("pagination_complete")
            return
        next_token_sha256 = hashlib.sha256(
            next_token.encode("utf-8")
        ).hexdigest()
        if next_token_sha256 != known_suppression_sha256:
            clear_known_repeated_cursor_suppression("continuation_changed")

    def should_request_quote_cursor(cursor: str) -> bool:
        if (
            not suppression_is_active
            or not known_suppression_sha256
            or hashlib.sha256(cursor.encode("utf-8")).hexdigest()
            != known_suppression_sha256
        ):
            return True
        retry_after_epoch = int(
            suppressions.get(post_id, {}).get("retry_after_epoch", 0) or 0
        )
        log.debug(
            "Skipping actively suppressed quote continuation post_id=%s "
            "token_fingerprint=%s retry_after_epoch=%s",
            post_id,
            known_suppression_sha256[:16],
            retry_after_epoch,
        )
        return False

    def retain_partial_quote_lookup(
        repeated_token: str,
        pages_completed: int,
        results_retained: int,
    ) -> None:
        nonlocal suppressions, known_suppression_sha256, suppression_is_active
        detected_epoch = now_epoch()
        retry_after_epoch = (
            detected_epoch + QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS
        )
        token_sha256 = hashlib.sha256(
            repeated_token.encode("utf-8")
        ).hexdigest()
        if state is not None:
            pagination_tokens.pop(post_id, None)
            state["quote_lookup_pagination_tokens"] = dict(pagination_tokens)
            suppressions[post_id] = {
                "cursor_sha256": token_sha256,
                "detected_epoch": detected_epoch,
                "retry_after_epoch": retry_after_epoch,
            }
            suppressions, _discarded = normalise_quote_repeated_cursor_suppressions(
                suppressions,
                current_epoch=detected_epoch,
            )
            state["quote_lookup_repeated_cursor_suppressions"] = dict(
                suppressions
            )
            save_state(state, durable=True)
        known_suppression_sha256 = token_sha256
        suppression_is_active = state is not None
        token_fingerprint = token_sha256[:16]
        log.warning(
            "Quote pagination stopped after repeated token post_id=%s "
            "token_fingerprint=%s pages_completed=%d results_retained=%d "
            "retry_after_epoch=%d",
            post_id,
            token_fingerprint,
            pages_completed,
            results_retained,
            retry_after_epoch,
        )
        log_event(
            "quote_pagination_repeated_token",
            post_id=post_id,
            token_fingerprint=token_fingerprint,
            pages_completed=pages_completed,
            results_retained=results_retained,
            backoff_seconds=QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS,
            retry_after_epoch=retry_after_epoch,
        )

    result = x_paginated_get(
        x_quote_lookup_request,
        f"/2/tweets/{post_id}/quote_tweets",
        params,
        max_pages=QUOTE_LOOKUP_MAX_PAGES_PER_POST,
        label=f"quote tweets for {post_id}",
        on_invalid_cursor=clear_invalid_quote_lookup_cursor,
        on_repeated_cursor=retain_partial_quote_lookup,
        on_page=observe_quote_lookup_page,
        should_request_cursor=should_request_quote_cursor,
    )
    pagination = result.get("_pagination", {}) if isinstance(result.get("_pagination", {}), dict) else {}
    if state is not None:
        next_token = str(pagination.get("next_token") or "")
        if next_token:
            pagination_tokens[post_id] = next_token
            state["quote_lookup_pagination_tokens"] = pagination_tokens
            log.warning("Quote lookup for post_id=%s truncated; saved pagination continuation token", post_id)
        elif post_id in pagination_tokens:
            pagination_tokens.pop(post_id, None)
            state["quote_lookup_pagination_tokens"] = pagination_tokens
            log.info("Quote lookup for post_id=%s reached end of pagination; cleared continuation token", post_id)

    quote_tweets = result.get("data", [])
    for tweet in quote_tweets:
        normalise_tweet_text(tweet)
    attach_media_to_tweets(quote_tweets, result.get("includes", {}))

    users_by_id = {
        str(user.get("id")): user
        for user in result.get("includes", {}).get("users", [])
    }

    for quote_tweet in quote_tweets:
        author_id = str(quote_tweet.get("author_id", ""))
        quote_tweet["_author_user"] = users_by_id.get(author_id, {})

    log.info("Fetched %d quote tweet(s) for post_id=%s", len(quote_tweets), post_id)
    log_json_debug("Quote tweets returned", quote_tweets)

    return quote_tweets


def get_quote_tweets_for_posts(
    post_ids: list[str],
    state: dict | None = None,
    *,
    QUOTE_LOOKUP_API_MAX_RESULTS: int,
    QUOTE_LOOKUP_MAX_PAGES_PER_POST: int,
    log: Logger,
    save_state: Callable,
    x_paginated_get: Callable,
    x_quote_lookup_request: Callable,
) -> dict[str, list[dict]]:
    """Search recent direct quotes together, retaining query-specific continuations.

    Recent search covers quotes created in the last seven days, including quotes
    of older watched originals. Do not advance a since_id: young, deferred and
    unprocessed quotes must remain discoverable on later checks.
    """
    clean_ids = list(dict.fromkeys(str(post_id).strip() for post_id in post_ids))
    clean_ids = [
        post_id for post_id in clean_ids
        if post_id.isascii() and (bounded_tweet_id_value(post_id) or 0) > 0
    ]
    quotes_by_post: dict[str, list[dict]] = {post_id: [] for post_id in clean_ids}
    # Ten bounded IDs fit within the 512-character recent-search query limit.
    # Canonical ordering keeps a cursor usable when only watch priority changes.
    sorted_ids = sorted(clean_ids)
    batches = [sorted_ids[index:index + 10] for index in range(0, len(sorted_ids), 10)]
    def query_for(batch: list[str]) -> str:
        """Build the canonical recent-search query for one batch."""
        return "(" + " OR ".join(f"quotes_of_tweet_id:{post_id}" for post_id in batch) + ") -is:retweet"

    queries = {query_for(batch) for batch in batches}
    queries.update(query_for([post_id]) for post_id in clean_ids)
    raw_tokens = state.get("quote_search_pagination_tokens", {}) if state is not None else {}
    tokens = {
        query: token for query, token in raw_tokens.items()
        if query in queries and isinstance(token, str) and token
    } if isinstance(raw_tokens, dict) else {}

    def persist_tokens() -> None:
        """Save changed search cursors without touching legacy endpoint state."""
        if state is not None and state.get("quote_search_pagination_tokens", {}) != tokens:
            state["quote_search_pagination_tokens"] = dict(tokens)
            save_state(state, durable=True)

    persist_tokens()
    seen_ids: set[str] = set()
    completed_tokens: dict[str, str] = {}
    def fetch_batch(batch: list[str]) -> dict:
        """Read one bounded search, clearing invalid saved cursors immediately."""
        query = query_for(batch)
        params = {
            "query": query,
            "sort_order": "recency",
            "max_results": QUOTE_LOOKUP_API_MAX_RESULTS,
            "tweet.fields": "author_id,created_at,conversation_id,referenced_tweets,attachments,entities,note_tweet",
            "expansions": "author_id,attachments.media_keys",
            "user.fields": "description,username,name,public_metrics",
            "media.fields": "media_key,type,url,preview_image_url",
        }
        if query in tokens:
            params["pagination_token"] = tokens[query]

        def clear_cursor() -> None:
            """Discard a rejected or repeated search continuation before retrying."""
            tokens.pop(query, None)
            persist_tokens()

        def retain_partial_search(_token: str, pages: int, count: int) -> None:
            """Keep discovered quotes while stopping a repeated search cursor."""
            clear_cursor()
            log.warning(
                "Quote search stopped after repeated cursor post_ids=%s pages=%d results_retained=%d",
                batch, pages, count,
            )

        return x_paginated_get(
            x_quote_lookup_request,
            "/2/tweets/search/recent",
            params,
            # Preserve the previous total page allowance for the watched posts.
            max_pages=QUOTE_LOOKUP_MAX_PAGES_PER_POST * len(batch),
            label=f"quote search for {','.join(batch)}",
            on_invalid_cursor=clear_cursor,
            on_repeated_cursor=retain_partial_search,
        )

    def retain_batch(batch: list[str], result: dict) -> None:
        """Stage accepted candidates and continuations until all searches succeed."""
        next_token = result.get("_pagination", {}).get("next_token")
        if next_token:
            completed_tokens[query_for(batch)] = next_token

        quotes = result.get("data", [])
        includes = result.get("includes", {})
        for tweet in quotes:
            normalise_tweet_text(tweet)
        attach_media_to_tweets(quotes, includes)
        users = {str(user.get("id")): user for user in includes.get("users", [])}
        for quote in quotes:
            quote_id = str(quote.get("id", ""))
            refs = quote.get("referenced_tweets", [])
            if (
                bounded_tweet_id_value(quote_id) is None
                or quote_id in seen_ids
                or not isinstance(refs, list)
                or any(not isinstance(ref, dict) for ref in refs)
                or any(ref.get("type") == "retweeted" for ref in refs)
            ):
                continue
            parents = {str(ref.get("id")) for ref in refs if ref.get("type") == "quoted"}
            if len(parents) != 1:
                continue
            parent_id = next(iter(parents))
            if parent_id not in batch:
                continue
            seen_ids.add(quote_id)
            quote["_author_user"] = users.get(str(quote.get("author_id", "")), {})
            quotes_by_post[parent_id].append(quote)

    for batch in batches:
        # Finish any per-original continuation before attempting another shared
        # head. A legacy combined continuation can also hide priority originals.
        split_search = len(batch) > 1 and (
            query_for(batch) in tokens
            or any(query_for([post_id]) in tokens for post_id in batch)
        )
        if not split_search:
            result = fetch_batch(batch)
            pagination = result.get("_pagination", {})
            if len(batch) == 1 or not pagination.get("truncated"):
                retain_batch(batch, result)
                continue
            log.info(
                "Combined quote search incomplete; searching %d originals separately to preserve watch priority",
                len(batch),
            )
        # Global recency can fill a shared page allowance with a lower-priority
        # original. Use the previous per-original allowance before replying.
        for post_id in batch:
            retain_batch([post_id], fetch_batch([post_id]))

    # Do not advance an earlier batch if a later search fails and the caller
    # receives none of its candidates. Invalid cursors are still cleared early.
    tokens = completed_tokens
    persist_tokens()
    log.info("Fetched %d direct quote tweet(s) by recent search for %d own post(s)", len(seen_ids), len(clean_ids))
    for post_id, quotes in quotes_by_post.items():
        log.info("Fetched %d quote tweet(s) for post_id=%s", len(quotes), post_id)
    return quotes_by_post

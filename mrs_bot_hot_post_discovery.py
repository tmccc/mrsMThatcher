"""Own hot-post discovery, skip marking and candidate handoff.

Four root adapters supply current callbacks, settings, clock and logger per
call. Fixed copying and bounded-list transformations are local. Original bodies
stage watched tracking maps in an owned operation before search and preserve
check counts, full rescans, nested invalid-cursor cleanup, eligibility before the candidate
cap, draft/terminal ordering, in-place annotations and conservative watermarks.
Skip records and mention/hot-post merges retain their bounded and copy behavior.

Watched-ID reading, pagination/authentication, target eligibility, draft and
terminal evaluation authority, cache/persistence, mention watermarks and reply
cycles remain in their existing locations. Runtime reads and saves use supplied
callbacks. This owner has no reverse application import, retained dependencies,
configuration, clients or state, and performs no import-time file, environment,
provider, clock or RNG work.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from logging import Logger
from pathlib import Path

from mrs_bot_reply_native_media import attach_media_to_tweets
from mrs_bot_reply_state import handled_reply_target_ids, retire_ineligible_reply_draft
from mrs_bot_runtime_state_helpers import append_unique_capped
from mrs_bot_tweet_lookup_cache import normalise_tweet_text


def _prune_unwatched_tracking(
    watched_post_ids: list[str],
    since_ids: dict,
    check_counts: dict,
    pagination_tokens: dict,
    *,
    log: Logger,
) -> tuple[dict, dict, dict, bool]:
    """Stage watched-post maps without publishing changes to caller state."""
    state_changed = False
    watched_post_id_set = {str(post_id) for post_id in watched_post_ids}
    pruned_since_ids = {str(post_id): value for post_id, value in since_ids.items() if str(post_id) in watched_post_id_set}
    pruned_check_counts = {
        str(post_id): value
        for post_id, value in check_counts.items()
        if str(post_id) in watched_post_id_set
    }
    pruned_pagination_tokens = {
        str(post_id): value
        for post_id, value in pagination_tokens.items()
        if str(post_id) in watched_post_id_set
    }
    if (
        pruned_since_ids != since_ids
        or pruned_check_counts != check_counts
        or pruned_pagination_tokens != pagination_tokens
    ):
        log.info(
            "Pruned hot-post reply tracking maps. since_ids=%d->%d check_counts=%d->%d pagination_tokens=%d->%d",
            len(since_ids),
            len(pruned_since_ids),
            len(check_counts),
            len(pruned_check_counts),
            len(pagination_tokens),
            len(pruned_pagination_tokens),
        )
        state_changed = True
    since_ids = pruned_since_ids
    check_counts = pruned_check_counts
    pagination_tokens = pruned_pagination_tokens

    return since_ids, check_counts, pagination_tokens, state_changed


def get_hot_post_reply_candidates(
    state: dict,
    *,
    ApiError: type[Exception],
    ENABLE_HOT_POST_REPLY_CHECKS: bool,
    EXTRA_QUOTE_WATCH_FILE: Path,
    HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS: int,
    HOT_POST_REPLY_SEARCH_API_MAX_RESULTS: int,
    HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK: int,
    HOT_POST_REPLY_USE_SINCE_ID: bool,
    MAX_HOT_POST_REPLIES_PER_CHECK: int,
    MY_USER_ID: str,
    cache_tweet: Callable,
    retire_ineligible_draft: Callable[[dict, str, str], None],
    in_api_cooldown: Callable,
    lane_paused: Callable,
    load_extra_quote_watch_post_ids: Callable,
    log: Logger,
    log_event: Callable,
    log_json_debug: Callable,
    mark_hot_post_reply_skipped: Callable,
    record_terminal_reply_evaluation: Callable,
    reply_target_is_directly_eligible: Callable,
    save_state: Callable,
    valid_tweets_sorted_by_id: Callable,
    x_paginated_get: Callable,
    x_quote_lookup_request: Callable,
) -> list[dict]:
    """
    Fetch directly reply-eligible contributions in conversations for watched hot posts.

    This deliberately uses the same extra_quote_watch_post_ids.txt file as the
    quote-tweet watch lane, so adding one hot post ID makes both lanes watch it.
    X permits a reply only when this account authored or is directly mentioned
    in the target post. Organic sub-thread chatter is therefore terminally
    skipped before applying the candidate cap, while eligible candidates are
    marked with _source=hot_post_reply and passed through the normal reply path.

    A soft per-hot-post since_id watermark is used only after a search returns
    no usable candidates. That avoids repeatedly downloading the same dead
    results, while preserving response ability when there is anything actionable.
    A full rescan is forced every HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS passes.
    """
    if not ENABLE_HOT_POST_REPLY_CHECKS:
        return []

    if lane_paused("disable_replies", "disable_hot_post_replies"):
        log.info("Skipping hot-post reply search due to runtime control file")
        return []

    if in_api_cooldown(state, scope="quote"):
        log.info("Skipping hot-post reply search due to quote API cooldown")
        return []

    try:
        watched_post_ids = load_extra_quote_watch_post_ids()
    except Exception as exc:
        log.warning("Could not load hot-post reply watch IDs: %s", exc)
        return []

    if not watched_post_ids:
        return []

    skipped_hot_reply_ids = set(str(x) for x in state.get("skipped_hot_reply_ids", []))
    replied_to_ids = handled_reply_target_ids(state)

    since_ids = state.get("hot_post_reply_since_ids", {})
    if not isinstance(since_ids, dict):
        since_ids = {}

    check_counts = state.get("hot_post_reply_check_counts", {})
    if not isinstance(check_counts, dict):
        check_counts = {}

    pagination_tokens = state.get("hot_post_reply_pagination_tokens", {})
    if not isinstance(pagination_tokens, dict):
        pagination_tokens = {}

    candidates: list[dict] = []

    since_ids, check_counts, pagination_tokens, state_changed = _prune_unwatched_tracking(
        watched_post_ids, since_ids, check_counts, pagination_tokens, log=log,
    )

    log.info(
        "Hot-post reply check loaded %d watched post(s) from %s: %s",
        len(watched_post_ids),
        EXTRA_QUOTE_WATCH_FILE,
        ", ".join(watched_post_ids),
    )

    for original_post_id in watched_post_ids:
        if len(candidates) >= MAX_HOT_POST_REPLIES_PER_CHECK:
            break

        original_post_id = str(original_post_id)
        previous_count = int(check_counts.get(original_post_id, 0) or 0)
        if HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS > 0:
            current_count = (previous_count % HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS) + 1
        else:
            current_count = previous_count + 1
        check_counts[original_post_id] = current_count
        state_changed = True

        full_rescan = False
        if HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS > 0:
            full_rescan = current_count == HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS
        if full_rescan and original_post_id in pagination_tokens:
            pagination_tokens.pop(original_post_id, None)
            state_changed = True

        # conversation_id finds replies in the original post's conversation.
        # We exclude this account and retweets; later filtering keeps only actual replies.
        query = f"conversation_id:{original_post_id} -from:{MY_USER_ID} -is:retweet"

        params = {
            "query": query,
            "max_results": HOT_POST_REPLY_SEARCH_API_MAX_RESULTS,
            "tweet.fields": "author_id,created_at,conversation_id,referenced_tweets,attachments,entities,note_tweet",
            "expansions": "author_id,attachments.media_keys",
            "media.fields": "media_key,type,url,preview_image_url",
        }

        since_id_used = ""
        if HOT_POST_REPLY_USE_SINCE_ID and not full_rescan:
            candidate_since_id = str(since_ids.get(original_post_id, "") or "")
            if candidate_since_id:
                params["since_id"] = candidate_since_id
                since_id_used = candidate_since_id
                log.info(
                    "Hot-post reply search for post_id=%s using since_id=%s",
                    original_post_id,
                    since_id_used,
                )

        resume_token = str(pagination_tokens.get(original_post_id, "") or "")
        if resume_token and not full_rescan:
            params["pagination_token"] = resume_token
            log.info("Resuming hot-post reply pagination for post_id=%s", original_post_id)

        def clear_invalid_hot_post_cursor() -> None:
            pagination_tokens.pop(original_post_id, None)
            state["hot_post_reply_pagination_tokens"] = dict(pagination_tokens)
            save_state(state, durable=True)

        try:
            result = x_paginated_get(
                x_quote_lookup_request,
                "/2/tweets/search/recent",
                params,
                max_pages=HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK,
                label=f"hot-post replies for {original_post_id}",
                on_invalid_cursor=clear_invalid_hot_post_cursor,
            )
        except ApiError:
            log.exception("Failed to fetch hot-post replies for post %s", original_post_id)
            raise
        except Exception:
            log.exception("Unexpected failure fetching hot-post replies for post %s", original_post_id)
            raise

        replies = result.get("data", [])
        for tweet in replies:
            normalise_tweet_text(tweet)
        attach_media_to_tweets(replies, result.get("includes", {}))
        pagination = result.get("_pagination", {}) if isinstance(result.get("_pagination", {}), dict) else {}
        pagination_truncated = bool(pagination.get("truncated"))
        next_pagination_token = str(pagination.get("next_token") or "")
        log.info("Fetched %d hot-post conversation candidate(s) for post_id=%s", len(replies), original_post_id)
        log_json_debug("Hot-post reply candidates returned", replies)

        valid_replies = valid_tweets_sorted_by_id(replies, context="hot-post reply candidate")
        raw_ids = [int(str(reply["id"])) for reply in valid_replies]
        raw_highest_id = str(max(raw_ids)) if raw_ids else ""
        candidates_for_this_post = 0

        for reply in valid_replies:
            reply_id = str(reply.get("id", ""))
            author_id = str(reply.get("author_id", ""))

            if not reply_id or reply_id == str(original_post_id):
                continue

            if reply_id in replied_to_ids or reply_id in skipped_hot_reply_ids:
                log.info("Skipping hot-post reply %s: already replied/skipped", reply_id)
                continue

            if author_id == str(MY_USER_ID):
                log.info("Skipping hot-post reply %s: authored by own account", reply_id)
                mark_hot_post_reply_skipped(
                    state,
                    reply_id,
                    reason="own_account",
                    original_post_id=str(original_post_id),
                    retryable=False,
                )
                state_changed = True
                continue

            refs = reply.get("referenced_tweets", []) or []
            if not any(ref.get("type") == "replied_to" for ref in refs):
                log.info(
                    "Skipping hot-post candidate %s: not an ordinary reply. referenced_tweets=%s",
                    reply_id,
                    refs,
                )
                mark_hot_post_reply_skipped(
                    state,
                    reply_id,
                    reason="not_ordinary_reply",
                    original_post_id=str(original_post_id),
                    retryable=False,
                )
                state_changed = True
                continue

            if not reply_target_is_directly_eligible(reply):
                reason = "target_does_not_directly_mention_account"
                log.info(
                    "Skipping hot-post reply %s before candidate limiting: "
                    "target is not directly reply-eligible",
                    reply_id,
                )
                retire_ineligible_reply_draft(
                    state,
                    reply_id,
                    "hot_post_reply",
                    reason=reason,
                    retire_draft=retire_ineligible_draft,
                    record_terminal_reply_evaluation=record_terminal_reply_evaluation,
                )
                mark_hot_post_reply_skipped(
                    state,
                    reply_id,
                    reason=reason,
                    original_post_id=original_post_id,
                    retryable=False,
                )
                log_event(
                    "reply_target_terminal",
                    lane="hot_post_reply",
                    target_id=reply_id,
                    outcome="reply_not_permitted",
                    reason=reason,
                )
                state_changed = True
                continue

            reply["_source"] = "hot_post_reply"
            reply["_hot_original_post_id"] = str(original_post_id)

            cache_tweet(
                state,
                tweet_id=reply_id,
                text=reply.get("text", ""),
                author_id=author_id,
                conversation_id=str(reply.get("conversation_id", reply_id)),
                referenced_tweets=reply.get("referenced_tweets", []),
                created_at=reply.get("created_at"),
                post_type="hot_post_reply",
            )

            candidates.append(reply)
            candidates_for_this_post += 1
            state_changed = True

            if len(candidates) >= MAX_HOT_POST_REPLIES_PER_CHECK:
                break

        watermark_updated = False
        if HOT_POST_REPLY_USE_SINCE_ID and raw_highest_id and candidates_for_this_post == 0 and not pagination_truncated:
            old_since = str(since_ids.get(original_post_id, "") or "")
            if not old_since or int(raw_highest_id) > int(old_since):
                since_ids[original_post_id] = raw_highest_id
                watermark_updated = True
                state_changed = True
                log.info(
                    "Updated hot-post reply since_id for post_id=%s to %s after no usable candidates",
                    original_post_id,
                    raw_highest_id,
                )
        if pagination_truncated:
            log.warning(
                "Not updating hot-post reply since_id for post_id=%s because pagination was truncated",
                original_post_id,
            )
            if next_pagination_token:
                pagination_tokens[original_post_id] = next_pagination_token
                state_changed = True
        elif original_post_id in pagination_tokens:
            pagination_tokens.pop(original_post_id, None)
            state_changed = True

        log_event(
            "hot_search",
            lane="hot_post",
            original_post_id=original_post_id,
            raw_count=len(replies),
            usable_count=candidates_for_this_post,
            since_id_used=since_id_used or None,
            full_rescan=full_rescan,
            watermark_updated=watermark_updated,
            watermark=since_ids.get(original_post_id),
        )

    state["hot_post_reply_since_ids"] = since_ids
    state["hot_post_reply_check_counts"] = check_counts
    state["hot_post_reply_pagination_tokens"] = pagination_tokens

    if state_changed:
        save_state(state)

    log.info("Hot-post reply check returning %d candidate(s)", len(candidates))
    return candidates


def mark_hot_post_reply_skipped(
    state: dict,
    reply_id: str,
    *,
    reason: str='unspecified',
    original_post_id: str | None=None,
    retryable: bool | None=None,
    log_event: Callable,
    now_epoch: Callable,
) -> None:
    """Mark hot post reply skipped."""
    reply_id = str(reply_id)
    if not reply_id:
        return

    state["skipped_hot_reply_ids"] = append_unique_capped(
        state.get("skipped_hot_reply_ids", []),
        reply_id,
        2000,
    )

    if retryable is None:
        retryable = False

    records = state.get("skipped_hot_reply_records", {})
    if not isinstance(records, dict):
        records = {}

    records[reply_id] = {
        "reason": reason,
        "retryable": bool(retryable),
        "skipped_epoch": now_epoch(),
    }
    if original_post_id:
        records[reply_id]["original_post_id"] = str(original_post_id)

    # Keep the record map bounded without disturbing the older list field that
    # existing digest scripts already understand.
    if len(records) > 2500:
        trimmed_items = sorted(
            records.items(),
            key=lambda item: int(item[1].get("skipped_epoch", 0) or 0),
        )[-2000:]
        records = dict(trimmed_items)

    state["skipped_hot_reply_records"] = records
    log_event(
        "candidate_skipped",
        lane="hot_post",
        id=reply_id,
        reason=reason,
        original_post_id=original_post_id,
        retryable=bool(retryable),
    )


def maybe_mark_hot_post_reply_skipped(
    state: dict,
    candidate: dict,
    reason: str='unspecified',
    *,
    mark_hot_post_reply_skipped: Callable,
) -> None:
    """
    Mark a candidate as handled for the hot-post lane when appropriate.

    A tweet can arrive from both /mentions and the hot-post recent-search lane.
    When we de-duplicate those, we keep the normal mention object so
    last_seen_mention_id still advances, but annotate it with
    _also_hot_post_reply=True. If that mention is then skipped, this helper
    still records the ID in skipped_hot_reply_ids so the hot-post lane does not
    reconsider it later and spend a second model decision.
    """
    if candidate.get("_source") == "hot_post_reply" or candidate.get("_also_hot_post_reply"):
        mark_hot_post_reply_skipped(
            state,
            str(candidate.get("id", "")),
            reason=reason,
            original_post_id=str(candidate.get("_hot_original_post_id", "") or "") or None,
        )


def dedupe_reply_candidates(
    mentions: list[dict],
    hot_post_replies: list[dict],
    *,
    log: Logger,
) -> list[dict]:
    """
    Merge normal mention candidates and hot-post reply candidates by tweet ID.

    Normal mentions win when the same tweet is present in both sources. That
    preserves last_seen_mention_id handling and prevents the same tweet being
    sent to the model twice in one check. The retained mention is annotated as also
    belonging to the hot-post lane, so any skip decision is remembered for the
    hot-post search path too.
    """
    mention_by_id: dict[str, dict] = {}
    combined: list[dict] = []
    duplicate_mentions = 0
    duplicate_hot_post_replies = 0

    for mention in mentions:
        mention_id = str(mention.get("id", ""))
        if not mention_id:
            log.info("Dropping mention candidate with no id: %s", mention)
            continue

        if mention_id in mention_by_id:
            duplicate_mentions += 1
            log.info("Dropping duplicate mention candidate id=%s", mention_id)
            continue

        mention_by_id[mention_id] = mention
        combined.append(mention)

    seen_ids = set(mention_by_id)

    for hot_post_reply in hot_post_replies:
        reply_id = str(hot_post_reply.get("id", ""))
        if not reply_id:
            log.info("Dropping hot-post reply candidate with no id: %s", hot_post_reply)
            continue

        if reply_id in mention_by_id:
            duplicate_hot_post_replies += 1
            retained_mention = mention_by_id[reply_id]
            retained_mention["_also_hot_post_reply"] = True

            hot_original_post_id = hot_post_reply.get("_hot_original_post_id")
            if hot_original_post_id:
                retained_mention["_hot_original_post_id"] = str(hot_original_post_id)
            if (
                not retained_mention.get("referenced_tweets")
                and isinstance(hot_post_reply.get("referenced_tweets"), list)
            ):
                retained_mention["referenced_tweets"] = copy.deepcopy(
                    hot_post_reply["referenced_tweets"]
                )

            log.info(
                "Dropping duplicate hot-post reply candidate %s: already present as a normal mention; "
                "retained mention candidate marked as also_hot_post_reply",
                reply_id,
            )
            continue

        if reply_id in seen_ids:
            duplicate_hot_post_replies += 1
            log.info("Dropping duplicate hot-post reply candidate id=%s", reply_id)
            continue

        seen_ids.add(reply_id)
        combined.append(hot_post_reply)

    original_count = len(mentions) + len(hot_post_replies)
    if duplicate_mentions or duplicate_hot_post_replies or len(combined) != original_count:
        log.info(
            "Reply candidate de-duplication: mentions=%d hot_post_replies=%d combined=%d "
            "duplicate_mentions=%d duplicate_hot_post_replies=%d",
            len(mentions),
            len(hot_post_replies),
            len(combined),
            duplicate_mentions,
            duplicate_hot_post_replies,
        )

    return combined

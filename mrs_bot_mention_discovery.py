"""Own durable mention discovery, queue access and watermark retirement.

Four root adapters supply current callbacks, settings, clock, logger, modules
and exception authorities on each call; dependency-free removal is a root alias.
Original bodies retain queue recovery before provider work, bounded traversal,
nested page/reset callbacks, durable commit boundaries and record references.

Mention authority normalization, validation and reset primitives, the continuation
exception, shared pagination/authentication, cache/persistence, terminal and
quarantine evaluation and reply cycles remain in their existing locations.
Runtime requests and saves use supplied callbacks. This owner retains no
callbacks, configuration, clients or state and performs no import-time file,
environment, provider, clock or RNG work or reverse application import.
"""

from __future__ import annotations

from collections.abc import Callable
from logging import Logger
from pathlib import Path
from types import ModuleType


def pending_mention_candidates(
    state: dict,
    *,
    STATE_FILE: Path,
    save_state: Callable,
    valid_tweets_sorted_by_id: Callable,
    validate_pending_mention_candidate_authority: Callable,
) -> list[dict]:
    """Return the durable fetched-candidate queue, deduplicated by status ID."""
    usable, changed = validate_pending_mention_candidate_authority(
        state,
        path=STATE_FILE,
        recover_pending_identity=True,
    )
    if not usable:
        raise RuntimeError(
            "Mention pending-candidate authority cannot be recovered without "
            "a bounded watermark"
        )
    if changed:
        save_state(state, durable=True)
    pending = state.get("mention_pending_candidates", {})
    if not isinstance(pending, dict):
        return []
    return valid_tweets_sorted_by_id(
        list(pending.values()),
        context="durable pending mention",
    )


def remove_pending_mention_candidate(state: dict, mention_id: str) -> bool:
    """Remove one completely handled mention from the durable fetched queue."""
    pending = state.get("mention_pending_candidates")
    if not isinstance(pending, dict) or str(mention_id) not in pending:
        return False
    pending = dict(pending)
    pending.pop(str(mention_id), None)
    state["mention_pending_candidates"] = pending
    return True


def get_mentions(
    state: dict,
    *,
    ApiError: type[Exception],
    MAX_MENTIONS_PER_CHECK: int,
    MENTIONS_MAX_PAGES_PER_CHECK: int,
    MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT: int,
    MY_USER_ID: str,
    _MentionBacklogContinuationLimit: type[Exception],
    active_mention_backlog_reset_guard: Callable,
    api_error_is_invalid_pagination_cursor: Callable,
    attach_media_to_tweets: Callable,
    cache_tweet: Callable,
    copy: ModuleType,
    hashlib: ModuleType,
    log: Logger,
    log_event: Callable,
    log_json_debug: Callable,
    now_epoch: Callable,
    pending_mention_candidates: Callable,
    prune_completed_mention_quarantine_evaluations: Callable,
    save_state: Callable,
    terminal_reply_evaluation: Callable,
    update_last_seen_mention_id: Callable,
    valid_tweets_sorted_by_id: Callable,
    x_paginated_get: Callable,
    x_request: Callable,
) -> list[dict]:
    """Fetch, durably queue and resume bounded pages of direct mentions.

    A fetched page is saved before its cursor or completed-range watermark is
    committed.  The durable pending queue therefore owns candidates until the
    reply loop records each one as handled, including across process restarts.
    """
    queued = pending_mention_candidates(state)
    if queued:
        log.info("Using %d durably queued mention candidate(s) before further pagination", len(queued))
        return queued

    current = now_epoch()
    remaining_pages = max(1, int(MENTIONS_MAX_PAGES_PER_CHECK))
    reset_logged = False

    def reset_backlog(reason: str, *, token: str = "") -> None:
        nonlocal reset_logged
        backlog = state.get("mention_backlog", {})
        saved_since_id = (
            str(backlog.get("since_id", ""))
            if isinstance(backlog, dict)
            else str(state.get("last_seen_mention_id") or "")
        )
        pages_completed = (
            int(backlog.get("pages_completed", 0) or 0)
            if isinstance(backlog, dict)
            else 0
        )
        pending = state.get("mention_pending_candidates")
        discarded_candidates = len(pending) if isinstance(pending, dict) else 0
        state["mention_backlog_reset_guard"] = {
            "base_since_id": str(state.get("last_seen_mention_id") or ""),
            "head_traversal_started": False,
        }
        state["mention_backlog"] = {}
        state["mention_pagination"] = {}
        state["mention_pending_candidates"] = {}
        save_state(state, durable=True)
        if not reset_logged:
            log.warning(
                "Mention backlog continuation was reset reason=%s; watermark "
                "remains unchanged and %d pending candidate(s) were discarded",
                reason,
                discarded_candidates,
            )
            log_event(
                "mention_backlog_reset",
                reason=reason,
                since_id=saved_since_id or None,
                pages_completed=pages_completed,
                token_fingerprint=(
                    hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
                    if token
                    else None
                ),
            )
            reset_logged = True

    while remaining_pages > 0:
        watermark = str(state.get("last_seen_mention_id") or "")
        backlog = state.get("mention_backlog", {})
        if not isinstance(backlog, dict):
            backlog = {}

        # Migrate the exact legacy cursor retained by schema-v3/v4 reply
        # receipts into the richer traversal record on first use.
        legacy = state.get("mention_pagination", {})
        if not backlog and isinstance(legacy, dict):
            legacy_base = str(legacy.get("base_since_id", ""))
            legacy_token = str(legacy.get("next_token", "") or "")
            if legacy_token and legacy_base == watermark:
                backlog = {
                    "since_id": watermark,
                    "next_token": legacy_token,
                    "highest_mention_id": watermark,
                    "pages_completed": 0,
                    "started_epoch": current,
                    "seen_tokens": [],
                    "announced": True,
                }
                state["mention_backlog"] = backlog
                save_state(state, durable=True)

        if backlog and str(backlog.get("since_id", "")) != watermark:
            reset_backlog("since_id_mismatch")
            backlog = {}

        if not backlog:
            backlog = {
                "since_id": watermark,
                "next_token": "",
                "highest_mention_id": watermark,
                "pages_completed": 0,
                "started_epoch": current,
                "seen_tokens": [],
                "announced": False,
            }
            state["mention_backlog"] = backlog
            state["mention_pagination"] = {}
            reset_guard = active_mention_backlog_reset_guard(state)
            if reset_guard is not None and not reset_guard["head_traversal_started"]:
                state["mention_backlog_reset_guard"] = {
                    "base_since_id": watermark,
                    "head_traversal_started": True,
                }
            save_state(state, durable=True)

        base_since_id = str(backlog["since_id"])
        resume_token = str(backlog.get("next_token", "") or "")
        prior_seen_tokens = {
            str(token)
            for token in backlog.get("seen_tokens", [])
            if isinstance(token, str) and token
        }
        prior_pages_completed = int(backlog.get("pages_completed", 0) or 0)
        was_announced = bool(backlog.get("announced"))
        log.info(
            "Fetching mentions. since_id=%s resume=%s max_results=%s remaining_pages=%s",
            base_since_id or None,
            bool(resume_token),
            MAX_MENTIONS_PER_CHECK,
            remaining_pages,
        )

        params = {
            "max_results": MAX_MENTIONS_PER_CHECK,
            "tweet.fields": "author_id,created_at,conversation_id,referenced_tweets,attachments,entities",
            "expansions": "author_id,attachments.media_keys",
            "media.fields": "media_key,type,url,preview_image_url",
        }
        if base_since_id:
            params["since_id"] = base_since_id
        if resume_token:
            params["pagination_token"] = resume_token
            log.info("Resuming mention backlog from saved continuation token")

        traversal_completed = False
        traversal_added = 0
        completion_highest = str(backlog.get("highest_mention_id", "") or "")
        completion_pages = prior_pages_completed

        def persist_page(
            page_data: list[dict],
            includes: dict,
            next_token: str,
            request_token: str,
            _pages_this_call: int,
        ) -> None:
            nonlocal traversal_completed, traversal_added, completion_highest, completion_pages
            attach_media_to_tweets(page_data, includes)
            pending = state.get("mention_pending_candidates", {})
            if not isinstance(pending, dict):
                pending = {}
            pending = dict(pending)
            valid_page = valid_tweets_sorted_by_id(page_data, context="mention page")
            highest = str(
                state.get("mention_backlog", {}).get("highest_mention_id", "")
                if isinstance(state.get("mention_backlog"), dict)
                else ""
            )
            replied_ids = {
                str(value) for value in state.get("replied_to_ids", [])
            }
            for mention in valid_page:
                mention_id = str(mention["id"])
                if (
                    mention_id not in replied_ids
                    and terminal_reply_evaluation(state, mention_id) is None
                ):
                    pending.setdefault(mention_id, copy.deepcopy(mention))
                traversal_added += 1
                if not highest or int(mention_id) > int(highest):
                    highest = mention_id
                cache_tweet(
                    state,
                    tweet_id=mention_id,
                    text=mention.get("text", ""),
                    author_id=str(mention.get("author_id", "")),
                    conversation_id=str(mention.get("conversation_id", mention_id)),
                    referenced_tweets=mention.get("referenced_tweets", []),
                    created_at=mention.get("created_at"),
                )
            state["mention_pending_candidates"] = pending
            active = state.get("mention_backlog", {})
            if not isinstance(active, dict) or not active:
                raise RuntimeError("Mention backlog disappeared while persisting a fetched page")
            active = copy.deepcopy(active)
            seen_tokens = list(active.get("seen_tokens", []))
            if request_token and request_token not in seen_tokens:
                if len(seen_tokens) >= MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT:
                    reset_backlog(
                        "continuation_token_limit",
                        token=request_token,
                    )
                    raise _MentionBacklogContinuationLimit
                seen_tokens.append(request_token)
            active["seen_tokens"] = seen_tokens
            active["highest_mention_id"] = highest
            active["pages_completed"] = int(active.get("pages_completed", 0) or 0) + 1
            active["next_token"] = str(next_token or "")
            completion_highest = highest
            completion_pages = int(active["pages_completed"])
            if next_token:
                state["mention_backlog"] = active
                state["mention_pagination"] = {
                    "base_since_id": str(active["since_id"]),
                    "next_token": str(next_token),
                }
                save_state(state, durable=True)
                if active.get("announced"):
                    log_event(
                        "mention_backlog_progress",
                        since_id=active["since_id"] or None,
                        pages_completed=active["pages_completed"],
                        highest_mention_id=highest or None,
                        continuation_token_present=True,
                    )
                return

            reset_guard = active_mention_backlog_reset_guard(state)
            head_traversal_completed = bool(
                reset_guard is not None
                and reset_guard["head_traversal_started"]
            )
            if highest and (reset_guard is None or head_traversal_completed):
                update_last_seen_mention_id(state, highest)
            elif highest and reset_guard is not None:
                log.warning(
                    "Deferring mention watermark advancement after a reset "
                    "until a traversal from the collection head completes"
                )
            state["mention_backlog"] = {}
            state["mention_pagination"] = {}
            if head_traversal_completed:
                state["mention_backlog_reset_guard"] = {}
            prune_completed_mention_quarantine_evaluations(state)
            save_state(state, durable=True)
            traversal_completed = True
            if active.get("announced"):
                log_event(
                    "mention_backlog_completed",
                    since_id=active["since_id"] or None,
                    pages_completed=active["pages_completed"],
                    highest_mention_id=highest or None,
                    backlog_age_seconds=max(0, current - int(active["started_epoch"])),
                )

        def invalid_cursor() -> None:
            reset_backlog("invalid_continuation_token", token=resume_token)

        def repeated_cursor(token: str, pages: int, _results: int) -> None:
            pending = state.get("mention_pending_candidates")
            if pages > 0 and isinstance(pending, dict) and pending:
                log.warning(
                    "Mention backlog repeated its continuation token after a "
                    "persisted page; retaining pagination provenance until %d "
                    "pending candidate(s) are handled",
                    len(pending),
                )
                return
            reset_backlog("repeated_continuation_token", token=token)

        try:
            result = x_paginated_get(
                lambda path, page_params: x_request("GET", path, params=page_params),
                f"/2/users/{MY_USER_ID}/mentions",
                params,
                max_pages=remaining_pages,
                label="mentions",
                on_invalid_cursor=invalid_cursor,
                on_repeated_cursor=repeated_cursor,
                on_page=persist_page,
                initial_requested_tokens=prior_seen_tokens,
                retry_invalid_cursor_from_head=False,
            )
        except _MentionBacklogContinuationLimit:
            break
        except ApiError as exc:
            if resume_token and api_error_is_invalid_pagination_cursor(exc):
                break
            raise

        pagination = result.get("_pagination", {}) if isinstance(result.get("_pagination"), dict) else {}
        pages_fetched = int(pagination.get("pages_fetched", 0) or 0)
        remaining_pages = max(0, remaining_pages - pages_fetched)
        if pagination.get("repeated_token_detected"):
            break
        if pagination.get("truncated"):
            active = state.get("mention_backlog", {})
            next_token = str(pagination.get("next_token") or "")
            if not next_token or not isinstance(active, dict) or not active:
                reset_backlog("missing_continuation_token", token=next_token)
                break
            if not active.get("announced"):
                active = copy.deepcopy(active)
                active["announced"] = True
                state["mention_backlog"] = active
                save_state(state, durable=True)
                log_event(
                    "mention_backlog_started",
                    since_id=active["since_id"] or None,
                    pages_completed=active["pages_completed"],
                    highest_mention_id=active.get("highest_mention_id") or None,
                    continuation_token_present=True,
                    backlog_started_epoch=active["started_epoch"],
                )
            elif not was_announced or int(active.get("pages_completed", 0) or 0) == prior_pages_completed:
                log_event(
                    "mention_backlog_progress",
                    since_id=active["since_id"] or None,
                    pages_completed=active["pages_completed"],
                    highest_mention_id=active.get("highest_mention_id") or None,
                    continuation_token_present=True,
                )
            break

        if not traversal_completed or pages_fetched == 0:
            break
        # Only completion of a previously truncated traversal starts a new
        # range in the same check.  An ordinary completed traversal is already
        # the current range; immediately repeating it would refetch the same
        # leading page before its candidates have been retired.
        if (
            not resume_token
            or traversal_added == 0
            or not completion_highest
            or completion_pages <= 0
        ):
            break

    mentions = pending_mention_candidates(state)
    log.info("Fetched and durably queued %d mention candidate(s)", len(mentions))
    log_json_debug("Mentions returned", mentions)
    return mentions


def update_last_seen_mention_id(
    state: dict,
    mention_id: str,
    *,
    log: Logger,
) -> None:
    """Advance the durable mention watermark without moving it backwards."""
    previous = state.get("last_seen_mention_id")
    log.debug("Updating last_seen_mention_id. previous=%s new_candidate=%s", previous, mention_id)
    if previous is None:
        state["last_seen_mention_id"] = str(mention_id)
        return
    try:
        state["last_seen_mention_id"] = str(max(int(previous), int(mention_id)))
    except ValueError:
        state["last_seen_mention_id"] = str(mention_id)
    log.debug("last_seen_mention_id is now %s", state["last_seen_mention_id"])


def mark_mention_seen_if_applicable(
    state: dict,
    candidate: dict,
    *,
    log: Logger,
    remove_pending_mention_candidate: Callable,
    update_last_seen_mention_id: Callable,
) -> None:
    """Retire a durably queued mention, with legacy watermark compatibility."""
    if candidate.get("_source", "mention") == "mention" and remove_pending_mention_candidate(
        state,
        str(candidate.get("id", "")),
    ):
        return
    if candidate.get("_pagination_truncated"):
        log.warning(
            "Not advancing mention watermark for %s because mention pagination was truncated",
            candidate.get("id"),
        )
        return
    if candidate.get("_source", "mention") == "mention":
        update_last_seen_mention_id(state, str(candidate.get("id", "")))

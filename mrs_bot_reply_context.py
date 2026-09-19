"""Build verified parent paths and prepared single-call reply contexts.

Normal reply builders return canonical context and native media as separate
fields. Parent lookup budgets, media preparation order, copy boundaries and
structural diagnostic hashes remain local policies. Root adapters supply current
callbacks, settings, exceptions, clock and logger. Cache and media collection,
canonical schema validation, persistence and orchestration stay in their owners.
Import performs no file, environment, clock, provider or RNG work.
"""

from __future__ import annotations

from datetime import timezone

from collections.abc import Callable
from logging import Logger
from types import ModuleType

from mrs_bot_reply_cycle_interfaces import PreparedReplyContext


def get_immediate_parent_id(
    tweet: dict,
    *,
    ApiError: type[Exception],
    parse_tweet_id: Callable,
) -> str | None:
    """Return immediate parent ID."""
    referenced_tweets = tweet.get("referenced_tweets", [])
    if referenced_tweets is None:
        return None
    if not isinstance(referenced_tweets, list):
        raise ApiError("X tweet returned malformed referenced_tweets", service="x")

    for ref in referenced_tweets:
        if not isinstance(ref, dict):
            raise ApiError("X tweet returned malformed referenced_tweets", service="x")
        if ref.get("type") == "replied_to":
            parent_id = parse_tweet_id(ref.get("id"), context="parent reference")
            if parent_id is None:
                raise ApiError("X tweet returned malformed referenced_tweets", service="x")
            return str(parent_id)

    return None


def clean_text_for_reply_context(
    text: str,
    *,
    html: ModuleType,
    re: ModuleType,
) -> str:
    """Return normalised text for a bounded AI reply context."""
    text = html.unescape(text or "")
    text = re.sub(r"https?://\S+", "", text)
    text = " ".join(text.split())
    return text.strip()


def tweet_context_text(
    tweet: dict,
    *,
    clean_text_for_reply_context: Callable,
) -> str:
    """Return the tweet context text."""
    cleaned = clean_text_for_reply_context(tweet.get("text", ""))

    if cleaned:
        return cleaned

    image_summary = clean_text_for_reply_context(tweet.get("image_summary", ""))
    if image_summary:
        return f"[Image/meme summary: {image_summary}]"

    return ""


def trim_context_text(
    text: str,
    max_chars: int,
    *,
    clean_text_for_reply_context: Callable,
) -> str:
    """Trim context text."""
    text = clean_text_for_reply_context(text)

    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    prefix = text[:max_chars - 3].rsplit(" ", 1)[0].rstrip(".,;:")
    if not prefix:
        prefix = text[:max_chars - 3]
    return prefix + "..."


def build_parent_chain(
    mention: dict,
    state: dict,
    *,
    ApiError: type[Exception],
    THREAD_CONTEXT_MAX_DEPTH: int,
    THREAD_CONTEXT_MAX_NETWORK_FETCHES: int,
    tweet_text_is_complete: Callable,
    api_error_is_permanent_target_failure: Callable,
    get_immediate_parent_id: Callable,
    get_tweet_by_id_cached: Callable,
    log: Logger,
    log_json_debug: Callable,
    prune_tweet_cache: Callable,
) -> list[dict]:
    """Build bounded earlier-thread context for a reply candidate."""
    chain: list[dict] = []
    seen_ids: set[str] = set()
    network_fetches = 0

    parent_id = get_immediate_parent_id(mention)
    prune_tweet_cache(state)

    while parent_id and len(chain) < THREAD_CONTEXT_MAX_DEPTH:
        if parent_id in seen_ids:
            log.warning("Detected parent-chain loop at tweet_id=%s", parent_id)
            break

        seen_ids.add(parent_id)
        cache = state.get("tweet_cache", {})
        parent_is_cached = bool(
            isinstance(cache, dict) and cache.get(parent_id)
            and tweet_text_is_complete(cache[parent_id])
        )
        if not parent_is_cached:
            if network_fetches >= THREAD_CONTEXT_MAX_NETWORK_FETCHES:
                log.info(
                    "Stopping parent-chain network expansion after %d "
                    "uncached lookup(s)",
                    network_fetches,
                )
                break
            network_fetches += 1

        try:
            parent = get_tweet_by_id_cached(parent_id, state)
        except ApiError as exc:
            if api_error_is_permanent_target_failure(exc):
                log.warning(
                    "Parent tweet_id=%s is permanently unavailable with status=%s; continuing without it",
                    parent_id,
                    getattr(exc, "status_code", None),
                )
                break
            raise
        if not parent:
            log.info("Could not fetch/cache parent tweet_id=%s", parent_id)
            break

        chain.append(parent)
        parent_id = get_immediate_parent_id(parent)

    chain.reverse()
    log.info("Built parent chain with %d item(s)", len(chain))
    log_json_debug("Parent chain", chain)

    return chain


def is_our_auto_reply(
    tweet: dict | None,
    state: dict,
    *,
    MY_USER_ID: str,
) -> bool:
    """Return whether a post is one of this account's conversational replies."""

    if not tweet or str(tweet.get("author_id")) != str(MY_USER_ID):
        return False
    return str(tweet.get("id")) in {
        str(value) for value in state.get("own_auto_reply_ids", [])
    }


def _reply_context_post(
    tweet: dict,
    *,
    principal_author_id: str,
    maximum_chars: int,
    MY_USER_ID: str,
    trim_context_text: Callable,
    tweet_context_text: Callable,
) -> dict[str, str]:
    """Return one bounded visible post with its canonical participant role."""

    author_id = str(tweet.get("author_id") or "")
    if author_id == str(MY_USER_ID):
        role = "account"
    elif author_id and author_id == str(principal_author_id):
        role = "user"
    else:
        role = "other_user"
    return {
        "post_id": str(tweet.get("id") or ""),
        "author_role": role,
        "text": trim_context_text(tweet_context_text(tweet), maximum_chars),
    }


def _log_single_call_context_summary(
    label: str,
    prepared: PreparedReplyContext,
    *,
    hashlib: ModuleType,
    json: ModuleType,
    log: Logger,
) -> None:
    """Log only bounded structure and a digest, never model-facing prose or URLs."""

    context = prepared.context
    visible = context.get("visible_conversation")
    visible_rows = visible if isinstance(visible, list) else []
    visible_character_count = sum(
        len(str(row.get("text") or ""))
        for row in visible_rows
        if isinstance(row, dict)
    )
    media_context = prepared.media_context
    photos = (
        media_context.get("photos")
        if isinstance(media_context, dict)
        else None
    )
    media_count = len(photos) if isinstance(photos, list) else 0
    try:
        # Keep existing diagnostic hashes stable without carrying media in the
        # canonical context passed to the model or durable reply records.
        encoded = json.dumps(
            {**context, "_prepared_media_context": media_context},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        encoded = b"non-canonical-single-call-context"
    log.debug(
        "%s summary target_id=%s visible_turn_count=%d "
        "visible_character_count=%d quoted_subject_present=%s "
        "media_count=%d context_sha256=%s",
        label,
        str(context.get("target_id") or ""),
        len(visible_rows),
        visible_character_count,
        bool(context.get("quoted_post_id") or context.get("quoted_post")),
        media_count,
        hashlib.sha256(encoded).hexdigest(),
    )


def _directly_quoted_tweet_for_reply_context(
    candidate: dict,
    state: dict,
    *,
    include_media: bool = True,
    ApiError: type[Exception],
    api_error_is_permanent_target_failure: Callable,
    get_tweet_by_id_cached: Callable,
) -> dict | None:
    """Return one directly quoted post with native media metadata when available."""

    references = candidate.get("referenced_tweets", []) or []
    for reference in references:
        if not isinstance(reference, dict) or reference.get("type") != "quoted":
            continue
        quoted_id = str(reference.get("id") or "")
        if not quoted_id:
            continue
        try:
            quoted = get_tweet_by_id_cached(
                quoted_id,
                state,
                # Parent-cache records omit native attachment expansions.  A
                # direct quote must refresh once with media fields rather than
                # treating a text-only cache hit as proof of no image.
                include_media=include_media,
            )
        except ApiError as exc:
            if not api_error_is_permanent_target_failure(exc):
                raise
            return None
        return quoted
    return None


def _direct_quote_id(candidate: dict) -> str | None:
    """Return the candidate's one valid directly quoted post identity."""

    references = candidate.get("referenced_tweets", []) or []
    if not isinstance(references, list):
        return None
    for reference in references:
        if not isinstance(reference, dict) or reference.get("type") != "quoted":
            continue
        quoted_id = str(reference.get("id") or "")
        if quoted_id:
            return quoted_id
    return None


def _quoted_post_for_reply_context(
    candidate: dict,
    state: dict,
    *,
    principal_author_id: str,
    _directly_quoted_tweet_for_reply_context: Callable,
    _reply_context_post: Callable,
) -> dict[str, str] | None:
    """Return one directly quoted post for local fact retrieval."""

    quoted = _directly_quoted_tweet_for_reply_context(candidate, state)
    if quoted is None:
        return None
    post = _reply_context_post(
        quoted,
        principal_author_id=principal_author_id,
    )
    return post if post["post_id"] and post["text"] else None


def _parent_path_is_contiguous(
    path: list[dict],
    target: dict,
    *,
    get_immediate_parent_id: Callable,
) -> bool:
    """Return whether every retained turn directly parents the next turn."""

    complete = [*path, target]
    return all(
        str(get_immediate_parent_id(complete[index]) or "")
        == str(complete[index - 1].get("id") or "")
        for index in range(1, len(complete))
    )


def _parent_path_is_chronological(
    path: list[dict],
    target: dict,
    *,
    parse_x_datetime_to_epoch: Callable,
) -> bool:
    """Reject a verified parent path whose available timestamps run forward."""

    def verified_created_epoch(post: dict) -> int | None:
        epoch = parse_x_datetime_to_epoch(post.get("created_at"))
        cached_epoch = post.get("cached_epoch")
        # ``cache_tweet`` historically supplied the observation time when X
        # omitted created_at.  Do not mistake that local fallback for verified
        # post chronology.
        if type(cached_epoch) is int and epoch == cached_epoch:
            return None
        return epoch

    complete = [*path, target]
    for older, newer in zip(complete, complete[1:]):
        older_epoch = verified_created_epoch(older)
        newer_epoch = verified_created_epoch(newer)
        if (
            older_epoch is not None
            and newer_epoch is not None
            and older_epoch > newer_epoch
        ):
            return False
    return True


def build_context_for_reply_ai(
    mention: dict,
    state: dict,
    *,
    ALWAYS_FETCH_PARENT_FOR_CONTEXT: bool,
    ContextValidationError: type[Exception],
    REPLY_INCOMING_MAX_CHARS: int,
    SKIP_REPLIES_TO_OWN_AUTO_REPLIES: bool,
    THREAD_CONTEXT_MAX_DEPTH: int,
    _direct_quote_id: Callable,
    _directly_quoted_tweet_for_reply_context: Callable,
    _log_single_call_context_summary: Callable,
    _parent_path_is_chronological: Callable,
    _parent_path_is_contiguous: Callable,
    _reply_context_post: Callable,
    bound_visible_conversation: Callable,
    build_parent_chain: Callable,
    copy: ModuleType,
    current_utc_datetime: Callable,
    get_immediate_parent_id: Callable,
    is_our_auto_reply: Callable,
    log: Logger,
    reply_media_context_for_candidate: Callable,
    trim_context_text: Callable,
) -> PreparedReplyContext | None:
    """Build the verified parent-contiguous canonical single-call context."""

    mention_id = str(mention.get("id") or "")
    mention_text = trim_context_text(
        str(mention.get("text") or "").strip(),
        REPLY_INCOMING_MAX_CHARS,
    )
    author_id = str(mention.get("author_id") or "")
    root_id = str(mention.get("conversation_id") or mention_id)
    if not mention_id or not mention_text or not author_id or not root_id:
        log.warning("Reply candidate lacks usable identity or text target_id=%s", mention_id)
        return None

    chain: list[dict] = []
    if ALWAYS_FETCH_PARENT_FOR_CONTEXT:
        chain = build_parent_chain(mention, state)

    immediate_parent = chain[-1] if chain else None
    if SKIP_REPLIES_TO_OWN_AUTO_REPLIES and is_our_auto_reply(
        immediate_parent,
        state,
    ):
        log.info(
            "Skipping target_id=%s because its immediate parent is an own "
            "conversational reply",
            mention_id,
        )
        return None

    if root_id != mention_id:
        if not chain or str(chain[0].get("id") or "") != root_id:
            log.warning(
                "Verified parent path did not reach root; retaining the longest "
                "available contiguous suffix target_id=%s root_id=%s traversals=%s",
                mention_id,
                root_id,
                THREAD_CONTEXT_MAX_DEPTH,
            )
        if not _parent_path_is_contiguous(chain, mention):
            log.warning(
                "Verified parent path is not contiguous target_id=%s",
                mention_id,
            )
            return None
        if not _parent_path_is_chronological(chain, mention):
            log.warning(
                "Verified parent path contains a post later than its child "
                "target_id=%s",
                mention_id,
            )
            return None
    elif chain:
        log.warning(
            "Root target unexpectedly has a parent path target_id=%s",
            mention_id,
        )
        return None

    visible: list[dict[str, str]] = []
    for tweet in chain:
        post = _reply_context_post(
            tweet,
            principal_author_id=author_id,
        )
        if not post["post_id"] or not post["text"]:
            log.warning(
                "Discarding older context through an unusable parent turn "
                "target_id=%s",
                mention_id,
            )
            visible = []
            continue
        visible.append(post)
    target_turn = _reply_context_post(
        mention,
        principal_author_id=author_id,
        maximum_chars=REPLY_INCOMING_MAX_CHARS,
    )
    if not target_turn["post_id"] or not target_turn["text"]:
        return None
    visible.append(target_turn)

    directly_quoted_candidate = _directly_quoted_tweet_for_reply_context(
        mention, state
    )
    if _direct_quote_id(mention) and directly_quoted_candidate is None:
        log.warning(
            "Directly quoted post is unavailable; refusing incomplete context "
            "target_id=%s quoted_id=%s",
            mention_id,
            _direct_quote_id(mention),
        )
        return None
    quoted_candidate = directly_quoted_candidate
    if quoted_candidate is None and chain:
        ancestor_quote_id = _direct_quote_id(chain[0])
        quoted_candidate = _directly_quoted_tweet_for_reply_context(
            chain[0], state, include_media=True
        )
        if ancestor_quote_id and quoted_candidate is None:
            log.warning(
                "Ancestor quoted post is unavailable; refusing incomplete "
                "context target_id=%s quoted_id=%s",
                mention_id,
                ancestor_quote_id,
            )
            return None
    quoted_post = None
    quoted_post_id = None
    if quoted_candidate is not None:
        candidate_post = _reply_context_post(
            quoted_candidate,
            principal_author_id=author_id,
        )
        quoted_post_id = candidate_post["post_id"] or None
        if candidate_post["post_id"] and candidate_post["text"]:
            quoted_post = candidate_post

    quoted_post_relationship = None
    if directly_quoted_candidate is not None:
        quoted_post_relationship = "target_quote"
    elif quoted_candidate is not None:
        quoted_post_relationship = "root_quote"

    try:
        bounded_visible = bound_visible_conversation(
            visible,
            target_post_id=mention_id,
        )
    except ContextValidationError as exc:
        log.warning(
            "Verified parent path could not be bounded target_id=%s reason=%s",
            mention_id,
            exc,
        )
        return None
    visible = [
        {
            "post_id": turn["post_id"],
            "author_role": turn["role"],
            "text": turn["text"],
        }
        for turn in bounded_visible
    ]

    parent_thread = copy.deepcopy(visible[:-1])
    prepared_media_context = reply_media_context_for_candidate(
        mention,
        lane=str(mention.get("_source") or "mention"),
        target_id=mention_id,
        quoted_candidate=quoted_candidate,
    )
    context: dict[str, object] = {
        "target_id": mention_id,
        "thread_id": root_id,
        "root_post_id": root_id,
        "parent_post_id": get_immediate_parent_id(mention),
        "lane": str(mention.get("_source") or "mention"),
        "incoming_contribution": mention_text,
        "quoted_post": quoted_post,
        "quoted_post_id": quoted_post_id,
        "quoted_post_relationship": quoted_post_relationship,
        "parent_thread": parent_thread,
        "visible_conversation": visible,
        "visual_description": None,
        "clarification_request": None,
        "current_date": current_utc_datetime().astimezone(timezone.utc).strftime("%Y-%m-%d"),
        "target_author_id": author_id,
        "target_created_at": str(mention.get("created_at") or ""),
    }
    log.info(
        "Built single-call reply context target_id=%s turns=%d root_id=%s "
        "parent_id=%s",
        mention_id,
        len(visible),
        root_id,
        get_immediate_parent_id(mention),
    )
    prepared = PreparedReplyContext(context, prepared_media_context)
    _log_single_call_context_summary("Single-call reply context", prepared)
    return prepared

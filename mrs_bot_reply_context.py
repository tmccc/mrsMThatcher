"""Own verified parent paths and prepared normal and quote reply contexts.

ReplyContext receives lookup/cache and media owners plus current validation, clock
and policy boundaries without retaining caller state. Parent and quote lookup and
prepared media call those owners directly, including cache pruning. Parent traversal, quote selection,
structural admission, usable parent-suffix projection and canonical assembly call
owned methods.
Text cleanup uses local pure helpers. Cache sharing, lookup budgets, media
preparation order, copy boundaries and diagnostic hashes retain their existing
semantics. Import and construction perform no file, environment, clock, provider
or RNG work.
"""

from __future__ import annotations

import copy
import hashlib
import html
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from logging import Logger
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from mrs_bot_reply_cycle_interfaces import PreparedReplyContext
from mrs_bot_tweet_lookup_cache import TweetLookupCache, tweet_text_is_complete


if TYPE_CHECKING:
    from mrs_bot_core_contracts import RawTweet, ReplyContextData, ReplyRole, VisibleReplyTurn
    from mrsMThatcher2 import ApiError
    from mrs_bot_reply_native_media import ReplyMedia


class ParseTweetId(Protocol):
    """Parse one untrusted provider identity with its diagnostic label."""

    def __call__(self, value: object, *, context: str) -> int | None:
        """Return the bounded numeric identity or None."""
        ...


class BoundVisibleConversation(Protocol):
    """Bound verified turns while preserving the final target identity."""

    def __call__(
        self, turns: Sequence[Mapping[str, Any]], *, target_post_id: str,
    ) -> list[dict[str, str]]:
        """Return canonical bounded turns with role and text keys."""
        ...


def parse_x_datetime_to_epoch(value: object, *, log: Logger) -> int | None:
    """Parse a provider timestamp, preserving malformed-date diagnostics."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(cast(str, value).replace("Z", "+00:00"))
        return int(parsed.timestamp())
    except Exception:
        log.warning("Could not parse X datetime: %r", value)
        return None


def parse_tweet_id(value: object, *, context: str, log: Logger) -> int | None:
    """Parse one bounded numeric tweet ID for reply discovery and context."""
    value_str = str(value or "")
    if not re.fullmatch(r"\d{1,30}", value_str):
        log.warning("Skipping %s with invalid tweet id=%r", context, value)
        return None
    return int(value_str)


def valid_tweets_sorted_by_id(
    tweets: list[RawTweet], *, context: str, log: Logger,
) -> list[RawTweet]:
    """Deduplicate and sort a page of reply candidates by numeric ID."""
    valid: list[tuple[int, RawTweet]] = []
    seen_ids: set[int] = set()
    for tweet in tweets:
        tweet_id = parse_tweet_id(tweet.get("id"), context=context, log=log)
        if tweet_id is None:
            continue
        if tweet_id in seen_ids:
            log.warning("Dropping duplicate %s tweet id=%s from paged API results", context, tweet_id)
            continue
        seen_ids.add(tweet_id)
        valid.append((tweet_id, tweet))
    return [tweet for _, tweet in sorted(valid, key=lambda item: item[0])]


def clean_text_for_reply_context(text: str) -> str:
    """Return normalised text for a bounded AI reply context."""
    text = html.unescape(text or "")
    text = re.sub(r"https?://\S+", "", text)
    text = " ".join(text.split())
    return text.strip()


def tweet_context_text(tweet: RawTweet) -> str:
    """Return the tweet context text."""
    cleaned = clean_text_for_reply_context(cast(str, tweet.get("text", "")))

    if cleaned:
        return cleaned

    image_summary = clean_text_for_reply_context(cast(str, tweet.get("image_summary", "")))
    if image_summary:
        return f"[Image/meme summary: {image_summary}]"

    return ""


def trim_context_text(text: str, max_chars: int) -> str:
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


def _direct_quote_id(candidate: RawTweet) -> str | None:
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


@dataclass(frozen=True)
class ReplyContext:
    """Build verified context using current external boundaries and explicit state."""

    api_error: type[ApiError]
    parse_tweet_id: ParseTweetId
    maximum_parent_depth: int
    maximum_parent_network_fetches: int
    is_permanent_target_failure: Callable[[Exception], bool]
    tweets: TweetLookupCache
    log: Logger
    log_json_debug: Callable[[str, object], None]
    user_id: str
    parse_x_datetime_to_epoch: Callable[[object], int | None]
    always_fetch_parent: bool
    context_validation_error: type[Exception]
    incoming_maximum_chars: int
    maximum_visible_chars: int
    skip_own_auto_replies: bool
    bound_visible_conversation: BoundVisibleConversation
    current_utc_datetime: Callable[[], datetime]
    media: ReplyMedia
    default_post_maximum_chars: int

    def parent_id(self, tweet: RawTweet) -> str | None:
        """Return immediate parent ID."""
        referenced_tweets = tweet.get("referenced_tweets", [])
        if referenced_tweets is None:
            return None
        if not isinstance(referenced_tweets, list):
            raise self.api_error("X tweet returned malformed referenced_tweets", service="x")

        for ref in referenced_tweets:
            if not isinstance(ref, dict):
                raise self.api_error("X tweet returned malformed referenced_tweets", service="x")
            if ref.get("type") == "replied_to":
                parent_id = self.parse_tweet_id(ref.get("id"), context="parent reference")
                if parent_id is None:
                    raise self.api_error("X tweet returned malformed referenced_tweets", service="x")
                return str(parent_id)

        return None

    def parent_chain(self, mention: RawTweet, state: dict[str, object]) -> list[RawTweet]:
        """Build bounded earlier-thread context for a reply candidate."""
        chain: list[RawTweet] = []
        seen_ids: set[str] = set()
        network_fetches = 0

        parent_id = self.parent_id(mention)
        self.tweets.prune(state)

        while parent_id and len(chain) < self.maximum_parent_depth:
            if parent_id in seen_ids:
                self.log.warning("Detected parent-chain loop at tweet_id=%s", parent_id)
                break

            seen_ids.add(parent_id)
            cache = state.get("tweet_cache", {})
            parent_is_cached = bool(
                isinstance(cache, dict) and cache.get(parent_id)
                and tweet_text_is_complete(cache[parent_id])
            )
            if not parent_is_cached:
                if network_fetches >= self.maximum_parent_network_fetches:
                    self.log.info(
                        "Stopping parent-chain network expansion after %d "
                        "uncached lookup(s)",
                        network_fetches,
                    )
                    break
                network_fetches += 1

            try:
                parent = self.tweets.get_cached(parent_id, state)
            except self.api_error as exc:
                if self.is_permanent_target_failure(exc):
                    self.log.warning(
                        "Parent tweet_id=%s is permanently unavailable with status=%s; continuing without it",
                        parent_id,
                        getattr(exc, "status_code", None),
                    )
                    break
                raise
            if not parent:
                self.log.info("Could not fetch/cache parent tweet_id=%s", parent_id)
                break

            chain.append(parent)
            parent_id = self.parent_id(parent)

        chain.reverse()
        self.log.info("Built parent chain with %d item(s)", len(chain))
        self.log_json_debug("Parent chain", chain)

        return chain

    def is_our_auto_reply(self, tweet: RawTweet | None, state: dict[str, object]) -> bool:
        """Return whether a post is one of this account's conversational replies."""

        if not tweet or str(tweet.get("author_id")) != str(self.user_id):
            return False
        return str(tweet.get("id")) in {
            str(value) for value in cast(Iterable[object], state.get("own_auto_reply_ids", []))
        }

    def post(
        self,
        tweet: RawTweet,
        *,
        principal_author_id: str,
        maximum_chars: int,
    ) -> VisibleReplyTurn:
        """Return one bounded visible post with its canonical participant role."""

        author_id = str(tweet.get("author_id") or "")
        role: Literal["account", "user", "other_user"]
        if author_id == str(self.user_id):
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

    def log_summary(self, label: str, prepared: PreparedReplyContext) -> None:
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
        self.log.debug(
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

    def directly_quoted_tweet(
        self,
        candidate: RawTweet,
        state: dict[str, object],
        *,
        include_media: bool = True,
    ) -> RawTweet | None:
        """Return one directly quoted post with native media metadata when available."""

        references = candidate.get("referenced_tweets", []) or []
        for reference in cast(Iterable[object], references):
            if not isinstance(reference, dict) or reference.get("type") != "quoted":
                continue
            quoted_id = str(reference.get("id") or "")
            if not quoted_id:
                continue
            try:
                quoted = self.tweets.get_cached(
                    quoted_id,
                    state,
                    # Parent-cache records omit native attachment expansions.  A
                    # direct quote must refresh once with media fields rather than
                    # treating a text-only cache hit as proof of no image.
                    include_media=include_media,
                )
            except self.api_error as exc:
                if not self.is_permanent_target_failure(exc):
                    raise
                return None
            return quoted
        return None

    def quoted_post(
        self,
        candidate: RawTweet,
        state: dict[str, object],
        *,
        principal_author_id: str,
    ) -> VisibleReplyTurn | None:
        """Return one directly quoted post for local fact retrieval."""

        quoted = self.directly_quoted_tweet(candidate, state)
        if quoted is None:
            return None
        post = self.post(
            quoted,
            principal_author_id=principal_author_id,
            maximum_chars=self.default_post_maximum_chars,
        )
        return post if post["post_id"] and post["text"] else None

    def parent_path_is_contiguous(self, path: list[RawTweet], target: RawTweet) -> bool:
        """Return whether every retained turn directly parents the next turn."""

        complete = [*path, target]
        return all(
            str(self.parent_id(complete[index]) or "")
            == str(complete[index - 1].get("id") or "")
            for index in range(1, len(complete))
        )

    def parent_path_is_chronological(self, path: list[RawTweet], target: RawTweet) -> bool:
        """Reject a verified parent path whose available timestamps run forward."""

        def verified_created_epoch(post: RawTweet) -> int | None:
            epoch = self.parse_x_datetime_to_epoch(post.get("created_at"))
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

    def _visible_parent_turns(
        self,
        chain: list[RawTweet],
        mention: RawTweet,
        *,
        mention_id: str,
        author_id: str,
    ) -> list[VisibleReplyTurn] | None:
        """Render the usable parent suffix and target without copying post rows."""
        visible: list[VisibleReplyTurn] = []
        for tweet in chain:
            post = self.post(
                tweet,
                principal_author_id=author_id,
                maximum_chars=self.default_post_maximum_chars,
            )
            if not post["post_id"] or not post["text"]:
                self.log.warning(
                    "Discarding older context through an unusable parent turn "
                    "target_id=%s",
                    mention_id,
                )
                visible = []
                continue
            visible.append(post)
        target_turn = self.post(
            mention,
            principal_author_id=author_id,
            maximum_chars=self.incoming_maximum_chars,
        )
        if not target_turn["post_id"] or not target_turn["text"]:
            return None
        visible.append(target_turn)
        return visible

    def _parent_path_is_usable(
        self,
        chain: list[RawTweet],
        mention: RawTweet,
        *,
        mention_id: str,
        root_id: str,
    ) -> bool:
        """Admit only contiguous chronological paths, retaining root-gap diagnostics."""
        if root_id != mention_id:
            if not chain or str(chain[0].get("id") or "") != root_id:
                self.log.warning(
                    "Verified parent path did not reach root; retaining the longest "
                    "available contiguous suffix target_id=%s root_id=%s traversals=%s",
                    mention_id,
                    root_id,
                    self.maximum_parent_depth,
                )
            if not self.parent_path_is_contiguous(chain, mention):
                self.log.warning(
                    "Verified parent path is not contiguous target_id=%s",
                    mention_id,
                )
                return False
            if not self.parent_path_is_chronological(chain, mention):
                self.log.warning(
                    "Verified parent path contains a post later than its child "
                    "target_id=%s",
                    mention_id,
                )
                return False
        elif chain:
            self.log.warning(
                "Root target unexpectedly has a parent path target_id=%s",
                mention_id,
            )
            return False
        return True

    def build(self, mention: RawTweet, state: dict[str, object]) -> PreparedReplyContext | None:
        """Build the verified parent-contiguous canonical single-call context."""

        mention_id = str(mention.get("id") or "")
        mention_text = trim_context_text(
            str(mention.get("text") or "").strip(),
            self.incoming_maximum_chars,
        )
        author_id = str(mention.get("author_id") or "")
        root_id = str(mention.get("conversation_id") or mention_id)
        if not mention_id or not mention_text or not author_id or not root_id:
            self.log.warning("Reply candidate lacks usable identity or text target_id=%s", mention_id)
            return None

        chain: list[RawTweet] = []
        if self.always_fetch_parent:
            chain = self.parent_chain(mention, state)

        immediate_parent = chain[-1] if chain else None
        if self.skip_own_auto_replies and self.is_our_auto_reply(
            immediate_parent,
            state,
        ):
            self.log.info(
                "Skipping target_id=%s because its immediate parent is an own "
                "conversational reply",
                mention_id,
            )
            return None

        if not self._parent_path_is_usable(
            chain, mention, mention_id=mention_id, root_id=root_id,
        ):
            return None

        visible = self._visible_parent_turns(
            chain, mention, mention_id=mention_id, author_id=author_id,
        )
        if visible is None:
            return None

        directly_quoted_candidate = self.directly_quoted_tweet(
            mention, state
        )
        if _direct_quote_id(mention) and directly_quoted_candidate is None:
            self.log.warning(
                "Directly quoted post is unavailable; refusing incomplete context "
                "target_id=%s quoted_id=%s",
                mention_id,
                _direct_quote_id(mention),
            )
            return None
        quoted_candidate = directly_quoted_candidate
        if quoted_candidate is None and chain:
            ancestor_quote_id = _direct_quote_id(chain[0])
            quoted_candidate = self.directly_quoted_tweet(
                chain[0], state, include_media=True
            )
            if ancestor_quote_id and quoted_candidate is None:
                self.log.warning(
                    "Ancestor quoted post is unavailable; refusing incomplete "
                    "context target_id=%s quoted_id=%s",
                    mention_id,
                    ancestor_quote_id,
                )
                return None
        quoted_post = None
        quoted_post_id = None
        if quoted_candidate is not None:
            candidate_post = self.post(
                quoted_candidate,
                principal_author_id=author_id,
                maximum_chars=self.default_post_maximum_chars,
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
            bounded_visible = self.bound_visible_conversation(
                visible,
                target_post_id=mention_id,
            )
        except self.context_validation_error as exc:
            self.log.warning(
                "Verified parent path could not be bounded target_id=%s reason=%s",
                mention_id,
                exc,
            )
            return None
        visible = [
            {
                "post_id": turn["post_id"],
                "author_role": cast("ReplyRole", turn["role"]),
                "text": turn["text"],
            }
            for turn in bounded_visible
        ]

        parent_thread = copy.deepcopy(visible[:-1])
        prepared_media_context = self.media.context(
            mention,
            lane=str(mention.get("_source") or "mention"),
            target_id=mention_id,
            quoted_candidate=quoted_candidate,
        )
        context: ReplyContextData = {
            "target_id": mention_id,
            "thread_id": root_id,
            "root_post_id": root_id,
            "parent_post_id": self.parent_id(mention),
            "lane": str(mention.get("_source") or "mention"),
            "incoming_contribution": mention_text,
            "quoted_post": quoted_post,
            "quoted_post_id": quoted_post_id,
            "quoted_post_relationship": quoted_post_relationship,
            "parent_thread": parent_thread,
            "visible_conversation": visible,
            "visual_description": None,
            "clarification_request": None,
            "current_date": self.current_utc_datetime().astimezone(timezone.utc).strftime("%Y-%m-%d"),
            "target_author_id": author_id,
            "target_created_at": str(mention.get("created_at") or ""),
        }
        self.log.info(
            "Built single-call reply context target_id=%s turns=%d root_id=%s "
            "parent_id=%s",
            mention_id,
            len(visible),
            root_id,
            self.parent_id(mention),
        )
        prepared = PreparedReplyContext(context, prepared_media_context)
        self.log_summary("Single-call reply context", prepared)
        return prepared

    def build_quote(
        self,
        original_tweet: RawTweet,
        quote_tweet: RawTweet,
    ) -> PreparedReplyContext:
        """Build canonical text and quoted-subject context for a quote-tweet."""

        target_id = str(quote_tweet.get("id") or "")
        original_id = str(original_tweet.get("id") or "")
        author_id = str(quote_tweet.get("author_id") or "")
        target_turn = self.post(
            quote_tweet,
            principal_author_id=author_id,
            maximum_chars=self.incoming_maximum_chars,
        )
        original_turn: VisibleReplyTurn = {
            "post_id": original_id,
            "author_role": "account",
            "text": trim_context_text(
                tweet_context_text(original_tweet),
                max(1, self.maximum_visible_chars - len(target_turn["text"])),
            ),
        }
        visible_turns: list[VisibleReplyTurn] = [target_turn]
        quoted_post: VisibleReplyTurn | None = None
        parent_thread: list[VisibleReplyTurn] = []
        if original_turn["text"]:
            visible_turns.insert(0, original_turn)
            quoted_post = copy.deepcopy(original_turn)
            parent_thread = [copy.deepcopy(original_turn)]
        bounded_visible = self.bound_visible_conversation(
            visible_turns,
            target_post_id=target_id,
        )
        visible: list[VisibleReplyTurn] = [
            {
                "post_id": turn["post_id"],
                "author_role": cast("ReplyRole", turn["role"]),
                "text": turn["text"],
            }
            for turn in bounded_visible
        ]
        context: ReplyContextData = {
            "target_id": target_id,
            "thread_id": str(
                quote_tweet.get("conversation_id") or target_id
            ),
            "root_post_id": original_id,
            "parent_post_id": original_id,
            "lane": "quote_tweet",
            "incoming_contribution": target_turn["text"],
            "quoted_post": quoted_post,
            "quoted_post_id": original_turn["post_id"],
            "quoted_post_relationship": "target_quote",
            "parent_thread": parent_thread,
            "visible_conversation": visible,
            "visual_description": None,
            "clarification_request": None,
            "current_date": self.current_utc_datetime().astimezone(timezone.utc).strftime("%Y-%m-%d"),
            "target_author_id": author_id,
            "target_created_at": str(quote_tweet.get("created_at") or ""),
        }
        media_context = self.media.context(
            quote_tweet,
            lane="quote_tweet",
            target_id=target_id,
            quoted_candidate=original_tweet,
        )
        prepared = PreparedReplyContext(context, media_context)
        self.log_summary("Single-call quote-tweet context", prepared)
        return prepared

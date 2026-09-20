"""Own conversational clarification eligibility and confirmed repair history.

Detection, author windows and terminal-thread checks share the same completion
ledger. Confirmation conflict checks and recording remain separate operations so
callers preserve their existing state, cache and telemetry ordering. Context and
lookup capabilities, pipeline enablement, exception types and author windows are
bound per invocation; persistence and remote delivery remain with their owners.
Import constructs only fixed regexes and stopwords, without runtime access.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from mrs_bot_tweet_lookup_cache import tweet_text_is_complete


CLARIFICATION_CUE_RE = re.compile(
    r"\b(?:you\s+)?(?:did(?:n't|\s+not)|does(?:n't|\s+not)|have(?:n't|\s+not))\s+answer(?:ed)?\b"
    r"|\b(?:your|that|the)\s+(?:reply|answer)\s+(?:did(?:n't|\s+not)|does(?:n't|\s+not))\s+answer\b"
    r"|\b(?:that(?:'s|\s+is|\s+was)\s+)?not\s+(?:what|the\s+question)\s+(?:i\s+)?asked\b"
    r"|\banswer\s+(?:my|the)\s+question\b"
    r"|\b(?:you\s+)?(?:avoided|evaded)\s+(?:my|the)\s+question\b",
    re.IGNORECASE,
)
CLARIFICATION_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'-]{2,}")
CLARIFICATION_TOKEN_STOPWORDS = {
    "answer", "asked", "did", "does", "from", "have", "people", "question",
    "that", "the", "their", "then", "they", "this", "towards", "what", "when",
    "where", "which", "who", "with", "you", "your",
}


def clarification_thread_id(candidate: dict) -> str:
    """Return the clarification thread ID."""
    return str(candidate.get("conversation_id") or candidate.get("id") or "")


def _clarification_tokens(text: object) -> set[str]:
    """Return significant question words after removing account handles."""
    without_handles = re.sub(r"(?<![A-Za-z0-9_])@[A-Za-z0-9_]+", " ", str(text or ""))
    return {
        token.lower() for token in CLARIFICATION_TOKEN_RE.findall(without_handles)
        if token.lower() not in CLARIFICATION_TOKEN_STOPWORDS
    }


@dataclass(frozen=True)
class ClarificationReplies:
    """Recognise eligible repair requests and own their completed-thread ledger."""

    pipeline_enabled: Callable
    parent_id: Callable
    get_tweet_by_id_cached: Callable
    api_error_is_permanent_target_failure: Callable
    is_our_auto_reply: Callable
    api_error: type[Exception]
    invalid_receipt: type[Exception]
    window_seconds: int
    log_event: Callable

    def thread_is_terminal(self, state: dict, candidate: dict) -> bool:
        """Return whether clarification thread is terminal."""
        records = state.get("clarification_reply_records", {})
        return isinstance(records, dict) and clarification_thread_id(candidate) in records

    def author_used_recently(self, state: dict, author_id: str, *, current: int) -> bool:
        """Return the author used clarification recently."""
        records = state.get("clarification_reply_records", {})
        if not isinstance(records, dict):
            return False
        cutoff = int(current) - self.window_seconds
        for record in records.values():
            if not isinstance(record, dict) or str(record.get("author_id") or "") != str(author_id):
                continue
            try:
                if int(record.get("completed_epoch", 0) or 0) > cutoff:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    tokens = staticmethod(_clarification_tokens)

    def context(self, state: dict, candidate: dict, *, current: int) -> dict | None:
        """Return bounded repair metadata only for a direct follow-up to our confirmed reply."""
        if not self.pipeline_enabled() or self.thread_is_terminal(state, candidate):
            return None
        author_id = str(candidate.get("author_id") or "")
        if not author_id or self.author_used_recently(state, author_id, current=current):
            return None

        try:
            prior_bot_reply_id = self.parent_id(candidate)
        except self.api_error:
            return None
        if not prior_bot_reply_id or prior_bot_reply_id not in {
            str(item) for item in state.get("own_auto_reply_ids", [])
        }:
            return None

        cache = state.get("tweet_cache", {})
        if not isinstance(cache, dict):
            return None
        prior_bot_reply = cache.get(prior_bot_reply_id)
        if not self.is_our_auto_reply(prior_bot_reply, state):
            return None
        try:
            original_question_id = self.parent_id(prior_bot_reply)
        except self.api_error:
            return None
        original_question = cache.get(str(original_question_id or ""))
        if not isinstance(original_question, dict):
            return None
        if str(original_question.get("author_id") or "") != author_id:
            return None

        thread_id = clarification_thread_id(candidate)
        if not thread_id or str(original_question.get("conversation_id") or original_question_id) != thread_id:
            return None
        if not tweet_text_is_complete(original_question):
            try:
                original_question = self.get_tweet_by_id_cached(str(original_question_id), state)
            except self.api_error as exc:
                if self.api_error_is_permanent_target_failure(exc):
                    return None
                raise
            if (
                not isinstance(original_question, dict)
                or str(original_question.get("author_id") or "") != author_id
                or str(original_question.get("conversation_id") or original_question_id) != thread_id
            ):
                return None
        question_text = str(original_question.get("text") or "")
        incoming_text = str(candidate.get("text") or "")
        if "?" not in question_text:
            return None

        explicit_correction = bool(CLARIFICATION_CUE_RE.search(incoming_text))
        restated_question = "?" in incoming_text
        if restated_question:
            restated_question = bool(
                self.tokens(question_text) & self.tokens(incoming_text)
            )
        if not explicit_correction and not restated_question:
            return None

        return {
            "thread_id": thread_id,
            "prior_bot_reply_id": prior_bot_reply_id,
            "original_question_id": str(original_question_id),
            "question_text": question_text,
            "trigger": "explicit_correction" if explicit_correction else "restated_question",
        }

    def assert_no_conflict(
        self, state: dict, clarification: dict, *, reply_post_id: str,
    ) -> None:
        """Reject confirmation when the thread already names a different repair."""
        existing_records = state.get("clarification_reply_records", {})
        existing = existing_records.get(str(clarification["thread_id"])) if isinstance(existing_records, dict) else None
        if isinstance(existing, dict) and str(existing.get("reply_post_id") or "") != reply_post_id:
            raise self.invalid_receipt(
                f"clarification thread {clarification['thread_id']} already has a different completed repair"
            )

    def record_completed(
        self, state: dict, clarification: dict, *, author_id: str,
        target_id: str, reply_post_id: str, reply_epoch: int,
    ) -> None:
        """Record a confirmed repair once, retaining prior ledger records and events."""
        thread_id = str(clarification["thread_id"])
        records = state.get("clarification_reply_records", {})
        if not isinstance(records, dict):
            records = {}
        existing = records.get(thread_id)
        if not isinstance(existing, dict):
            records = dict(records)
            records[thread_id] = {
                "thread_id": thread_id,
                "author_id": author_id,
                "target_id": target_id,
                "reply_post_id": reply_post_id,
                "prior_bot_reply_id": str(clarification["prior_bot_reply_id"]),
                "original_question_id": str(clarification["original_question_id"]),
                "trigger": str(clarification["trigger"]),
                "completed_epoch": reply_epoch,
                "status": "repair_reply_completed",
                "clarification_reply_used": True,
                "thread_terminal": True,
            }
            state["clarification_reply_records"] = records
            self.log_event(
                "clarification_reply_used",
                thread_id=thread_id,
                author_id=author_id,
                target_id=target_id,
                reply_post_id=reply_post_id,
                trigger=clarification["trigger"],
            )
            self.log_event(
                "repair_reply_completed",
                thread_id=thread_id,
                author_id=author_id,
                target_id=target_id,
                reply_post_id=reply_post_id,
            )

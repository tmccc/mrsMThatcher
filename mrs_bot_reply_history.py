"""Own confirmed reply history recording and the history supplied to replies.

ReplyHistory binds current limits, quoted-post lookup and clocks without
retaining caller state. Fixed post-ID validation comes from receipt primitives. Recording preserves confirmed receipt metadata and
retention rules; evaluation and recovery use their distinct time boundaries.
Selectors call each other directly and retain existing row references and
ordering. Reconciliation owns durable saves, posting telemetry and receipt
retirement. Import and construction perform no runtime access.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from mrs_bot_receipt_primitives import valid_string_post_id


CONVERSATIONAL_REPLY_HISTORY_LANES = frozenset(
    {"mention", "hot_post_reply", "quote_tweet", "conversational_reply"}
)


def _confirmed_history_sort_key(row: dict) -> tuple[int, int]:
    """Order confirmed replies by time and numeric X post identity."""

    return (int(row["reply_epoch"]), int(str(row["reply_post_id"])))


def _reply_target_epoch(context: Mapping[str, object]) -> int | None:
    value = context.get("target_created_at")
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return int(parsed.timestamp())


@dataclass(frozen=True)
class ReplyHistory:
    """Record and select confirmed history using explicit caller state."""

    now_epoch: Callable
    quoted_post_reference_id: Callable
    maximum_state_epoch: int
    maximum_recent_replies: int
    default_recent_reply_limit: int
    maximum_same_author_interactions: int
    maximum_age_seconds: int
    maximum_records: int

    def confirmed_rows(self, state: dict) -> list[dict]:
        """Return positively identified rows from the durable confirmation history."""

        history = state.get("ai_reply_history", [])
        if not isinstance(history, list):
            return []
        rows: list[dict] = []
        for raw in history:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("candidate_source") or "") not in (
                CONVERSATIONAL_REPLY_HISTORY_LANES
            ):
                continue
            if raw.get("deleted") is True or str(raw.get("status") or "") in {
                "deleted",
                "failed",
                "pending",
            }:
                continue
            target_id = str(raw.get("target_id") or "")
            reply_post_id = str(raw.get("reply_post_id") or "")
            reply = str(raw.get("proposed_reply") or "").strip()
            epoch = raw.get("reply_epoch")
            if (
                not valid_string_post_id(target_id)
                or not valid_string_post_id(reply_post_id)
                or not reply
                or type(epoch) is not int
                or epoch <= 0
                or epoch > self.maximum_state_epoch
            ):
                continue
            rows.append(raw)
        return rows

    def recent_replies(
        self,
        state: dict,
        limit: int,
        *,
        before_epoch: int | None = None,
        excluded_post_ids: set[str] | None = None,
        excluded_reply_post_ids: set[str] | None = None,
    ) -> list[dict[str, str]]:
        """Return only prior remotely confirmed conversational account replies."""

        if before_epoch is None:
            return []
        excluded = {str(value) for value in (excluded_post_ids or set())}
        excluded.update(str(value) for value in (excluded_reply_post_ids or set()))
        rows = []
        for row in self.confirmed_rows(state):
            reply_post_id = str(row["reply_post_id"])
            if reply_post_id in excluded:
                continue
            if row["reply_epoch"] >= before_epoch:
                continue
            rows.append(row)
        rows.sort(key=_confirmed_history_sort_key)
        by_reply_id: dict[str, dict] = {}
        for row in rows:
            by_reply_id[str(row["reply_post_id"])] = row
        rows = sorted(by_reply_id.values(), key=_confirmed_history_sort_key)
        bounded_limit = max(0, min(int(limit), self.maximum_recent_replies))
        return [
            {
                "post_id": str(row["reply_post_id"]),
                "text": str(row["proposed_reply"]).strip(),
            }
            for row in rows[-bounded_limit:]
        ] if bounded_limit else []

    def context_excluded_post_ids(self, context: Mapping[str, Any]) -> set[str]:
        """Return every current subject identity excluded from history fields."""

        excluded_post_ids = {
            str(turn.get("post_id") or "")
            for turn in (context.get("visible_conversation") or [])
            if isinstance(turn, dict)
        }
        excluded_post_ids.update(
            {
                str(context.get("thread_id") or ""),
                str(context.get("root_post_id") or ""),
            }
        )
        quoted_post_id = self.quoted_post_reference_id(context)
        if quoted_post_id is not None:
            excluded_post_ids.add(quoted_post_id)
        excluded_post_ids.discard("")
        return excluded_post_ids

    def recovery_replies(
        self,
        state: dict,
        *,
        context: Mapping[str, Any],
    ) -> list[dict[str, str]]:
        """Return current confirmed prose used only to revalidate an unsent draft."""

        excluded_post_ids = self.context_excluded_post_ids(context)
        before_epoch = min(self.now_epoch() + 1, self.maximum_state_epoch + 1)
        recent = self.recent_replies(
            state,
            self.default_recent_reply_limit,
            before_epoch=before_epoch,
            excluded_post_ids=excluded_post_ids,
        )
        same_author_rows = self.same_author_rows(
            state,
            author_id=context.get("target_author_id"),
            current_thread_post_ids=excluded_post_ids,
            target_id=str(context.get("target_id") or ""),
            before_epoch=before_epoch,
        )
        by_reply_id: dict[str, Mapping[str, Any]] = {
            str(row.get("post_id") or ""): row
            for row in recent
            if isinstance(row, dict)
        }
        for row in same_author_rows:
            reply_id = str(row.get("reply_post_id") or "")
            if reply_id and reply_id not in by_reply_id:
                by_reply_id[reply_id] = {
                    "post_id": reply_id,
                    "text": str(row.get("proposed_reply") or ""),
                    "_reply_epoch": int(row.get("reply_epoch") or 0),
                }
        history_epochs = {
            str(row.get("reply_post_id") or ""): int(row.get("reply_epoch") or 0)
            for row in self.confirmed_rows(state)
        }
        return [
            {"post_id": reply_id, "text": str(row.get("text") or "")}
            for reply_id, row in sorted(
                by_reply_id.items(),
                key=lambda item: (
                    int(item[1].get("_reply_epoch") or history_epochs.get(item[0], 0)),
                    item[0],
                ),
            )
        ]

    def same_author_rows(
        self,
        state: dict,
        *,
        author_id: object,
        current_thread_post_ids: set[str],
        target_id: object,
        before_epoch: int | None,
    ) -> list[dict]:
        """Select genuine prior same-author pairs before dropping local identity."""

        wanted_author = str(author_id or "")
        current_target = str(target_id or "")
        if not wanted_author or before_epoch is None:
            return []
        latest_allowed = before_epoch
        earliest_allowed = latest_allowed - self.maximum_age_seconds
        rows: list[dict] = []
        for row in self.confirmed_rows(state):
            if str(row.get("author_id") or "") != wanted_author:
                continue
            identity_fields = {
                str(row.get("target_id") or ""),
                str(row.get("reply_post_id") or ""),
                str(row.get("conversation_id") or ""),
                str(row.get("root_post_id") or ""),
            }
            identity_fields.discard("")
            if (
                str(row.get("target_id") or "") == current_target
                or identity_fields.intersection(current_thread_post_ids)
            ):
                continue
            epoch = int(row["reply_epoch"])
            if epoch < earliest_allowed:
                continue
            if epoch >= before_epoch:
                continue
            contributor = str(row.get("incoming_contribution") or "").strip()
            if (
                not str(row.get("conversation_id") or "")
                or not contributor
                or hashlib.sha256(contributor.encode("utf-8")).hexdigest()
                != row.get("incoming_contribution_sha256")
            ):
                continue
            rows.append(row)
        rows.sort(key=_confirmed_history_sort_key)
        by_interaction: dict[tuple[str, str], dict] = {}
        for row in rows:
            identity = (str(row["target_id"]), str(row["reply_post_id"]))
            by_interaction[identity] = row
        return sorted(
            by_interaction.values(),
            key=_confirmed_history_sort_key,
        )[-self.maximum_same_author_interactions:]

    def recent_same_author_interactions(
        self,
        state: dict,
        *,
        author_id: object,
        conversation_id: object,
        target_id: object,
        before_epoch: int | None = None,
        visible_post_ids: set[str] | None = None,
    ) -> list[dict[str, str]]:
        """Return confirmed pairs from earlier conversations by this contributor."""

        current_thread_post_ids = {
            str(value) for value in (visible_post_ids or set()) if str(value)
        }
        current_thread_post_ids.add(str(conversation_id or ""))
        current_thread_post_ids.discard("")
        rows = self.same_author_rows(
            state,
            author_id=author_id,
            current_thread_post_ids=current_thread_post_ids,
            target_id=target_id,
            before_epoch=before_epoch,
        )
        return [
            {
                "contributor": str(row["incoming_contribution"]).strip(),
                "account_reply": str(row["proposed_reply"]).strip(),
            }
            for row in rows[-self.maximum_same_author_interactions:]
        ]

    def for_evaluation(
        self,
        state: dict,
        *,
        context: Mapping[str, object],
        target_id: str,
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """Return same-author interactions and other prose before the target post."""

        before_epoch = _reply_target_epoch(context)
        current_thread_post_ids = self.context_excluded_post_ids(context)
        same_author_rows = self.same_author_rows(
            state,
            author_id=context.get("target_author_id"),
            current_thread_post_ids=current_thread_post_ids,
            target_id=target_id,
            before_epoch=before_epoch,
        )
        same_author = [
            {
                "contributor": str(row["incoming_contribution"]).strip(),
                "account_reply": str(row["proposed_reply"]).strip(),
            }
            for row in same_author_rows
        ]
        recent_replies = self.recent_replies(
            state,
            self.default_recent_reply_limit,
            before_epoch=before_epoch,
            excluded_post_ids=current_thread_post_ids,
            excluded_reply_post_ids={
                str(row["reply_post_id"]) for row in same_author_rows
            },
        )
        return same_author, recent_replies

    def record_confirmation(
        self,
        state: dict,
        receipt: dict,
        draft: dict,
        *,
        target_id: str,
        reply_post_id: str,
        author_id: str,
        conversation_id: str,
        candidate_source: str,
        reply_epoch: int,
    ) -> None:
        """Record an already-confirmed draft using the caller's resolved identity."""

        reply_context = receipt.get("reply_context")
        visible = (
            reply_context.get("visible_conversation")
            if isinstance(reply_context, dict)
            else None
        )
        incoming_contribution = ""
        if isinstance(visible, list) and visible and isinstance(visible[-1], dict):
            incoming_contribution = str(visible[-1].get("text") or "").strip()
        if not incoming_contribution and isinstance(reply_context, dict):
            incoming_contribution = str(
                reply_context.get("incoming_contribution") or ""
            ).strip()
        record = {
            "target_id": target_id,
            "reply_post_id": reply_post_id,
            "author_id": author_id,
            "conversation_id": conversation_id,
            "incoming_contribution": incoming_contribution,
            "candidate_source": candidate_source,
            "reply_epoch": reply_epoch,
            **draft,
        }
        if receipt.get("schema_version") == 4:
            record["attempt_epoch"] = int(receipt["attempt_epoch"])
            record["confirmation_epoch"] = reply_epoch
        cutoff = max(reply_epoch, self.now_epoch()) - self.maximum_age_seconds
        history = [
            item
            for item in state.get("ai_reply_history", [])
            if (
                isinstance(item, dict)
                and str(item.get("reply_post_id") or "") != reply_post_id
                and str(item.get("target_id") or "") != target_id
                and type(item.get("reply_epoch")) is int
                and item["reply_epoch"] >= cutoff
            )
        ]
        history.append(record)
        history.sort(
            key=lambda item: (
                int(item["reply_epoch"]),
                int(str(item.get("reply_post_id") or "0"))
                if valid_string_post_id(item.get("reply_post_id"))
                else 0,
            )
        )
        state["ai_reply_history"] = history[-self.maximum_records:]

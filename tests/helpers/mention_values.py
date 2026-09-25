"""Synthetic mention and queue data without application bootstrap."""

from __future__ import annotations

import copy

DIGEST_AUTHOR_NO_REPLY_EVIDENCE_POLICY = (
    "single_sol_explicit_spam_or_abuse_v2"
)


DIGEST_AUTHOR_NO_REPLY_CONFIG = {
    "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD": 3,
    "AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS": 21_600,
    "AUTHOR_NO_REPLY_QUARANTINE_SECONDS": 43_200,
}


def mention(
    tweet_id: int, author_id: int, text: str = "@MrsMThatcher A contribution.",
    *, account_id: str = "12345",
) -> dict:
    """Return a complete synthetic mention using an explicit test account ID."""
    return {
        "id": str(tweet_id),
        "author_id": str(author_id),
        "conversation_id": str(tweet_id),
        "text": text,
        "text_is_complete": True,
        "entities": {
            "mentions": [
                {"id": str(account_id), "username": "MrsMThatcher"}
            ]
        },
        "referenced_tweets": [],
    }


def queue_active_mention(state: dict, candidate: dict, *, base_since_id: str) -> None:
    """Install one pending test candidate with exact active-page ownership."""
    state["last_seen_mention_id"] = base_since_id
    state["mention_backlog"] = {
        "since_id": base_since_id,
        "next_token": "A",
        "highest_mention_id": str(candidate["id"]),
        "pages_completed": 1,
        "started_epoch": 1_999_999_000,
        "seen_tokens": [],
        "announced": True,
    }
    state["mention_pagination"] = {
        "base_since_id": base_since_id,
        "next_token": "A",
    }
    state["mention_pending_candidates"] = {
        str(candidate["id"]): copy.deepcopy(candidate)
    }


def editorial_no_reply(
    context: dict,
    *_args: object,
    evaluation_outcome: dict | None = None,
    reason_code: str = "completed_exchange",
    **_kwargs: object,
) -> None:
    """Return one valid single-call editorial no-reply test outcome."""

    assert context.get("target_id")
    assert evaluation_outcome is not None
    evaluation_outcome.update(
        {
            "status": "no_reply",
            "reason": reason_code,
            "reason_code": reason_code,
            "model_call_count": 1,
        }
    )
    return None


def mention_backlog(
    *,
    since_id: str,
    next_token: str = "A",
    highest_mention_id: str = "105",
) -> dict:
    """Build one active mention-page ownership record."""
    return {
        "since_id": since_id,
        "next_token": next_token,
        "highest_mention_id": highest_mention_id,
        "pages_completed": 1,
        "started_epoch": 1_999_999_000,
        "seen_tokens": [],
        "announced": True,
    }


def digest_author_no_reply_record(
    epochs: list[int],
    *,
    quarantine_until_epoch: int = 0,
    last_updated_epoch: int | None = None,
) -> dict:
    """Build the exact current durable author-quarantine record shape."""
    updated = last_updated_epoch
    if updated is None:
        updated = max([*epochs, quarantine_until_epoch, 0])
    return {
        "recent_no_reply_epochs": list(epochs),
        "quarantine_until_epoch": quarantine_until_epoch,
        "last_updated_epoch": updated,
        "latest_explicit_spam_or_abuse_epoch": epochs[-1] if epochs else 0,
        "evidence_policy": DIGEST_AUTHOR_NO_REPLY_EVIDENCE_POLICY,
    }

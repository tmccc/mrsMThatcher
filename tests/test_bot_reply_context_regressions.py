"""Regression tests for bot reply context."""

from __future__ import annotations


import copy
import json
import hashlib
from datetime import datetime
from pathlib import Path

import pytest

from tests.helpers.bot_runtime import (
    bot,
)
from tests.helpers.bot_fixtures import (
    _configure_test_x_base,
    isolate_bot_runtime,
)
from tests.helpers.reply_fixtures import (
    patch_tweet_lookup_method,
    restore_tweet_lookup_fetch,
    UNIT_REPLY_REPOSITORY,
    patch_reply_context_method,
    unit_reply_context,
)
from tests.fake_api_server import FakeApiServer
from single_call_reply import (
    PipelineResult,
    STRATEGY_VERSION,
    build_model_payload,
    run_reply_pipeline as run_single_call_reply_pipeline,
)


pytestmark = pytest.mark.allow_loopback_network


def test_direct_tweet_lookup_rejects_a_mismatched_response_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bind a fetched row to the exact ID encoded in the request path."""

    monkeypatch.setattr(
        bot,
        "x_request",
        lambda *_args, **_kwargs: {
            "data": {
                "id": "901",
                "author_id": "200",
                "text": "A different post.",
            }
        },
    )
    restore_tweet_lookup_fetch(monkeypatch)

    with pytest.raises(bot.ApiError, match="mismatched post") as raised:
        bot.get_tweet_by_id("900")
    assert raised.value.request_path == "/2/tweets/900"


def test_cached_tweet_lookup_rejects_a_mismatched_row_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not trust a cache key when the cached row names another post."""

    state = bot.default_state()
    state["tweet_cache"] = {
        "900": {
            "id": "901",
            "author_id": "200",
            "conversation_id": "901",
            "created_at": "2026-09-04T12:00:00Z",
            "referenced_tweets": [],
            "text": "A different cached post.",
            "cached_epoch": bot.now_epoch(),
        }
    }
    patch_tweet_lookup_method(monkeypatch, "fetch", lambda *_args, **_kwargs: pytest.fail("a mismatched cache hit must fail closed"))

    with pytest.raises(bot.ApiError, match="mismatched post") as raised:
        bot.get_tweet_by_id_cached("900", state)
    assert raised.value.request_path == "/2/tweets/900"


def test_hot_post_reply_native_photo_context_is_retained_for_single_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = {
        "tweets": {
            "900": {
                "id": "900",
                "text": "Original watched post.",
                "author_id": "12345",
                "conversation_id": "900",
                "created_at": "2026-07-06T09:00:00Z",
            }
        },
        "search_recent": [
            {
                "id": "910",
                "text": "A hot reply with an image. https://t.co/example",
                "author_id": "310",
                "conversation_id": "900",
                "created_at": "2026-07-06T10:00:00Z",
                "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
                "referenced_tweets": [{"type": "replied_to", "id": "900"}],
                "attachments": {"media_keys": ["3_910"]},
            }
        ],
        "search_recent_extra": {
            "includes": {
                "media": [
                    {
                        "media_key": "3_910",
                        "type": "photo",
                        "url": "https://pbs.twimg.com/media/hot-photo.jpg",
                    }
                ]
            }
        },
        "grok_replies": ["SKIP"],
    }
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        watch_file = tmp_path / "extra_quote_watch_post_ids.txt"
        watch_file.write_text("900\n", encoding="utf-8")
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", watch_file)
        _configure_test_x_base(monkeypatch, server.url)
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", True)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")

        candidates = scenario["search_recent"]
        bot.attach_media_to_tweets(candidates, scenario["search_recent_extra"]["includes"])
        media = bot.reply_media_context_for_candidate(candidates[0], lane="hot_post_reply", target_id="910")
        assert media["photos"] == [{
            "media_key": "3_910",
            "url": "https://pbs.twimg.com/media/hot-photo.jpg",
            "attachment_role": "target_contribution",
            "source_post_id": "910",
        }]
    finally:
        server.stop()


def test_reply_images_prioritise_target_then_direct_quote_and_ignore_parent() -> None:
    def candidate(post_id: str, *media_keys: str) -> dict:
        return {
            "id": post_id,
            "_attached_media": [
                {
                    "media_key": media_key,
                    "type": "photo",
                    "url": f"https://pbs.twimg.com/media/{media_key}.jpg",
                }
                for media_key in media_keys
            ],
        }

    target = candidate("100", "target-1")
    quoted = candidate("90", "quoted-1", "quoted-2")
    unrelated_parent = candidate("80", "parent-1")

    media = bot.reply_media_context_for_candidate(
        target,
        lane="mention",
        target_id="100",
        quoted_candidate=quoted,
    )

    assert media["status"] == "supplied"
    assert [photo["media_key"] for photo in media["photos"]] == [
        "target-1",
        "quoted-1",
    ]
    assert len(media["photos"]) == 2
    assert [photo["attachment_role"] for photo in media["photos"]] == [
        "target_contribution",
        "quoted_subject",
    ]
    assert [photo["source_post_id"] for photo in media["photos"]] == [
        "100",
        "90",
    ]
    assert "parent-1" not in {
        photo["media_key"] for photo in media["photos"]
    }
    assert unrelated_parent["_attached_media"][0]["media_key"] == "parent-1"

    target_only = bot.reply_media_context_for_candidate(
        candidate("101", "target-1", "target-2", "target-3"),
        lane="mention",
        target_id="101",
        quoted_candidate=quoted,
    )
    assert [photo["media_key"] for photo in target_only["photos"]] == [
        "target-1",
        "target-2",
    ]

    incomplete = bot.reply_media_context_for_candidate(
        {
            "id": "102",
            "attachments": {"media_keys": ["target-missing"]},
        },
        lane="mention",
        target_id="102",
    )
    assert incomplete == {
        "lane": "mention",
        "target_id": "102",
        "mode": "multimodal",
        "status": "unavailable",
        "photos_expected": 1,
        "photos": [],
    }


def test_quote_tweet_context_wires_target_and_quoted_images_in_priority_order() -> None:
    def media(media_key: str) -> dict[str, str]:
        return {
            "media_key": media_key,
            "type": "photo",
            "url": f"https://pbs.twimg.com/media/{media_key}.jpg",
        }

    prepared_context = bot._reply_context_owner().build_quote(
        {
            "id": "900",
            "author_id": "12345",
            "text": "Quoted account post.",
            "_attached_media": [media("quoted-1"), media("quoted-2")],
        },
        {
            "id": "910",
            "author_id": "200",
            "conversation_id": "910",
            "text": "Target commentary.",
            "_attached_media": [media("target-1")],
        },
    )
    assert prepared_context is not None
    context = prepared_context.context

    prepared = prepared_context.media_context
    assert [photo["media_key"] for photo in prepared["photos"]] == [
        "target-1",
        "quoted-1",
    ]
    assert [photo["attachment_role"] for photo in prepared["photos"]] == [
        "target_contribution",
        "quoted_subject",
    ]
    assert [photo["source_post_id"] for photo in prepared["photos"]] == [
        "910",
        "900",
    ]
    assert context["visible_conversation"][-1]["post_id"] == "910"


def test_recent_account_replies_are_only_confirmed_conversational_replies() -> None:
    state = bot.default_state()
    state["ai_reply_history"] = [
        {
            "target_id": str(100 + index),
            "reply_post_id": str(9000 + index),
            "candidate_source": lane,
            "reply_epoch": index,
            "proposed_reply": text,
        }
        for index, lane, text in (
            (1, "mention", "Confirmed mention reply."),
            (2, "hot_post_reply", "Confirmed hot-post reply."),
            (3, "quote_tweet", "Confirmed quote-tweet reply."),
            (4, "conversational_reply", "Confirmed generic reply."),
            (5, "quote_image", "Quotation main post."),
            (6, "daily_meme", "Daily meme."),
            (7, "historical_context_reply", "Historical-context reply."),
        )
    ]
    state["ai_reply_history"].extend(
        [
            {
                "target_id": "108",
                "reply_post_id": "9008",
                "candidate_source": "mention",
                "reply_epoch": 8,
                "proposed_reply": "Deleted reply.",
                "deleted": True,
            },
            {
                "target_id": "109",
                "reply_post_id": "9009",
                "candidate_source": "mention",
                "reply_epoch": 9,
                "proposed_reply": "Failed reply.",
                "status": "failed",
            },
            {
                "target_id": "110",
                "candidate_source": "mention",
                "reply_epoch": 10,
                "proposed_reply": "Unconfirmed reply attempt.",
            },
            {
                "target_id": "111",
                "reply_post_id": "9011",
                "candidate_source": "mention",
                "reply_epoch": 50,
                "proposed_reply": "Reply later than the current target.",
            },
        ]
    )
    state["pending_ai_reply_drafts"] = {
        "mention:112": {"proposed_reply": "Pending model draft."}
    }
    state["recent_own_post_ids"] = ["8001", "8002"]
    state["tweet_cache"] = {
        "8001": {"post_type": "quote", "text": "Cached quotation post."},
        "8002": {"post_type": "daily_meme", "text": "Cached meme post."},
    }

    assert bot.recent_confirmed_account_replies(
        state,
        before_epoch=20,
        excluded_post_ids={"9002"},
    ) == [
        {"post_id": "9001", "text": "Confirmed mention reply."},
        {"post_id": "9003", "text": "Confirmed quote-tweet reply."},
        {"post_id": "9004", "text": "Confirmed generic reply."},
    ]
    assert bot.recent_confirmed_account_replies(state) == []


def test_recent_conversational_replies_take_latest_thirty_chronologically() -> None:
    state = bot.default_state()
    state["ai_reply_history"] = [
        {
            "target_id": str(1000 + index),
            "reply_post_id": str(9000 + index),
            "candidate_source": "mention",
            "reply_epoch": index,
            "proposed_reply": f"Confirmed reply {index}.",
        }
        for index in range(1, 36)
    ]

    replies = bot.recent_confirmed_account_replies(state, before_epoch=40)

    assert [row["post_id"] for row in replies] == [
        str(9000 + index) for index in range(6, 36)
    ]


def test_same_author_history_is_bound_prior_deduplicated_and_cross_thread() -> None:
    state = bot.default_state()
    eligible = [
        {
            "target_id": str(100 + index),
            "reply_post_id": str(9000 + index),
            "author_id": "200",
            "conversation_id": str(700 + index),
            "root_post_id": str(700 + index),
            "incoming_contribution": f"Contributor {index}.",
            "incoming_contribution_sha256": hashlib.sha256(
                f"Contributor {index}.".encode("utf-8")
            ).hexdigest(),
            "candidate_source": "mention",
            "reply_epoch": index,
            "proposed_reply": f"Account reply {index}.",
        }
        for index in range(1, 11)
    ]
    state["ai_reply_history"] = [
        *eligible,
        copy.deepcopy(eligible[-1]),
        {
            **eligible[0],
            "target_id": "300",
            "reply_post_id": "9300",
            "conversation_id": "current-root",
            "root_post_id": "current-root",
            "incoming_contribution": "Same current thread.",
            "incoming_contribution_sha256": hashlib.sha256(
                b"Same current thread."
            ).hexdigest(),
            "reply_epoch": 20,
        },
        {
            **eligible[0],
            "target_id": "301",
            "reply_post_id": "9301",
            "author_id": "201",
            "incoming_contribution": "Another author.",
            "incoming_contribution_sha256": hashlib.sha256(
                b"Another author."
            ).hexdigest(),
            "reply_epoch": 21,
        },
        {
            **eligible[0],
            "target_id": "302",
            "reply_post_id": "9302",
            "incoming_contribution": "Later interaction.",
            "incoming_contribution_sha256": hashlib.sha256(
                b"Later interaction."
            ).hexdigest(),
            "reply_epoch": 100,
        },
        {
            **eligible[0],
            "target_id": "303",
            "reply_post_id": "",
            "incoming_contribution": "Unconfirmed draft.",
            "incoming_contribution_sha256": hashlib.sha256(
                b"Unconfirmed draft."
            ).hexdigest(),
            "reply_epoch": 22,
        },
    ]

    interactions = bot.recent_same_author_account_interactions(
        state,
        author_id="200",
        conversation_id="current-conversation",
        target_id="current-target",
        before_epoch=50,
        visible_post_ids={"current-root", "current-target"},
    )

    assert interactions == [
        {
            "contributor": f"Contributor {index}.",
            "account_reply": f"Account reply {index}.",
        }
        for index in range(3, 11)
    ]
    assert bot.recent_same_author_account_interactions(
        state,
        author_id="200",
        conversation_id="current-conversation",
        target_id="current-target",
        visible_post_ids={"current-root", "current-target"},
    ) == []


def test_generation_does_not_duplicate_same_author_reply_in_recent_replies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = unit_reply_context(target_id="500", thread_id="500")
    target_epoch = int(datetime.fromisoformat("2026-07-20T12:00:00+00:00").timestamp())
    state = bot.default_state()
    state["ai_reply_history"] = [
        {
            "target_id": "100",
            "reply_post_id": "9001",
            "author_id": "200",
            "conversation_id": "100",
            "root_post_id": "100",
            "incoming_contribution": "An earlier contribution.",
            "incoming_contribution_sha256": hashlib.sha256(
                b"An earlier contribution."
            ).hexdigest(),
            "candidate_source": "mention",
            "reply_epoch": target_epoch - 20,
            "proposed_reply": "Same-author confirmed reply.",
        },
        {
            "target_id": "200",
            "reply_post_id": "9002",
            "author_id": "201",
            "conversation_id": "200",
            "root_post_id": "200",
            "incoming_contribution": "Someone else's contribution.",
            "incoming_contribution_sha256": hashlib.sha256(
                b"Someone else's contribution."
            ).hexdigest(),
            "candidate_source": "hot_post_reply",
            "reply_epoch": target_epoch - 10,
            "proposed_reply": "Other confirmed conversational reply.",
        },
    ]
    captured: dict[str, object] = {}

    def pipeline(**kwargs: object) -> PipelineResult:
        captured.update(kwargs)
        return PipelineResult(
            status="no_reply",
            reason="completed_exchange",
            decision="no_reply",
            reply_kind="no_reply",
            reason_code="completed_exchange",
            model_call_count=1,
            local_validation_status="passed",
        )

    monkeypatch.setattr(bot, "run_single_call_reply_pipeline", pipeline)
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda *_args: None)
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    assert bot.generate_single_call_reply(context, None, state=state) is None
    assert captured["same_author_interactions"] == [
        {
            "contributor": "An earlier contribution.",
            "account_reply": "Same-author confirmed reply.",
        }
    ]
    assert captured["recent_account_replies"] == [
        {
            "post_id": "9002",
            "text": "Other confirmed conversational reply.",
        }
    ]


def test_generation_excludes_quoted_target_from_same_author_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not repeat a separately quoted prior contribution as history."""

    context = unit_reply_context(target_id="500", thread_id="500")
    context["quoted_post"] = {
        "post_id": "100",
        "author_role": "user",
        "text": "An earlier contribution now quoted directly.",
    }
    context["quoted_post_id"] = "100"
    context["quoted_post_relationship"] = "target_quote"
    target_epoch = int(
        datetime.fromisoformat("2026-07-20T12:00:00+00:00").timestamp()
    )
    contribution = "An earlier contribution now quoted directly."
    state = bot.default_state()
    state["ai_reply_history"] = [
        {
            "target_id": "100",
            "reply_post_id": "9001",
            "author_id": "200",
            "conversation_id": "100",
            "root_post_id": "100",
            "incoming_contribution": contribution,
            "incoming_contribution_sha256": hashlib.sha256(
                contribution.encode("utf-8")
            ).hexdigest(),
            "candidate_source": "mention",
            "reply_epoch": target_epoch - 20,
            "proposed_reply": "Earlier confirmed account reply.",
        }
    ]
    captured: dict[str, object] = {}

    def pipeline(**kwargs: object) -> PipelineResult:
        captured.update(kwargs)
        return PipelineResult(
            status="no_reply",
            reason="completed_exchange",
            decision="no_reply",
            reply_kind="no_reply",
            reason_code="completed_exchange",
            model_call_count=1,
            local_validation_status="passed",
        )

    monkeypatch.setattr(bot, "run_single_call_reply_pipeline", pipeline)
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda *_args: None)
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    assert bot.generate_single_call_reply(context, None, state=state) is None
    assert captured["same_author_interactions"] == []


def test_generation_excludes_quoted_account_reply_from_recent_replies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not repeat a separately quoted bot reply in the recent list."""

    context = unit_reply_context(target_id="500", thread_id="500")
    context["quoted_post"] = {
        "post_id": "9002",
        "author_role": "account",
        "text": "A prior bot reply now quoted directly.",
    }
    context["quoted_post_id"] = "9002"
    context["quoted_post_relationship"] = "target_quote"
    target_epoch = int(
        datetime.fromisoformat("2026-07-20T12:00:00+00:00").timestamp()
    )
    state = bot.default_state()
    state["ai_reply_history"] = [
        {
            "target_id": "100",
            "reply_post_id": "9002",
            "author_id": "201",
            "conversation_id": "100",
            "root_post_id": "100",
            "incoming_contribution": "Someone else's earlier contribution.",
            "incoming_contribution_sha256": hashlib.sha256(
                b"Someone else's earlier contribution."
            ).hexdigest(),
            "candidate_source": "mention",
            "reply_epoch": target_epoch - 20,
            "proposed_reply": "A prior bot reply now quoted directly.",
        }
    ]
    captured: dict[str, object] = {}

    def pipeline(**kwargs: object) -> PipelineResult:
        captured.update(kwargs)
        return PipelineResult(
            status="no_reply",
            reason="completed_exchange",
            decision="no_reply",
            reply_kind="no_reply",
            reason_code="completed_exchange",
            model_call_count=1,
            local_validation_status="passed",
        )

    monkeypatch.setattr(bot, "run_single_call_reply_pipeline", pipeline)
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda *_args: None)
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    assert bot.generate_single_call_reply(context, None, state=state) is None
    assert captured["recent_account_replies"] == []


def test_long_parent_context_never_truncates_away_incoming_contribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incoming = "INCOMING-ISSUE-MARKER responsibility for local government"
    mention = {
        "id": "900",
        "author_id": "200",
        "text": incoming,
        "conversation_id": "1",
        "referenced_tweets": [{"type": "replied_to", "id": "5"}],
    }
    chain = [
        {
            "id": str(index),
            "author_id": str(100 + index),
            "text": f"parent-{index} " + ("inherited context " * 80),
            "referenced_tweets": (
                [{"type": "replied_to", "id": str(index - 1)}]
                if index > 1 else []
            ),
        }
        for index in range(1, 6)
    ]
    monkeypatch.setattr(bot, "ALWAYS_FETCH_PARENT_FOR_CONTEXT", True)
    monkeypatch.setattr(bot, "SKIP_REPLIES_TO_OWN_AUTO_REPLIES", False)
    patch_reply_context_method(monkeypatch, "parent_chain", lambda _mention, _state: chain)

    prepared_context = bot._reply_context_owner().build(mention, bot.default_state())
    assert prepared_context is not None
    context = prepared_context.context

    assert context["incoming_contribution"] == incoming
    assert len(context["parent_thread"]) == 5
    assert context["parent_thread"][0]["post_id"] == "1"
    assert context["parent_thread"][-1]["post_id"] == "5"
    assert context["visible_conversation"][-1]["post_id"] == "900"
    assert sum(
        len(turn["text"]) for turn in context["visible_conversation"]
    ) <= bot.MAX_VISIBLE_TEXT_CHARACTERS


def test_fifteen_turn_linear_thread_reaches_root_then_bounds_visible_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mention = {
        "id": "15",
        "author_id": "200",
        "text": "Final target contribution.",
        "conversation_id": "1",
        "created_at": "2026-09-04T12:15:00Z",
        "referenced_tweets": [{"type": "replied_to", "id": "14"}],
    }
    chain = [
        {
            "id": str(index),
            "author_id": "12345" if index == 1 else "200",
            "text": f"Linear turn {index}.",
            "conversation_id": "1",
            "created_at": f"2026-09-04T12:{index:02d}:00Z",
            "referenced_tweets": (
                [{"type": "replied_to", "id": str(index - 1)}]
                if index > 1
                else []
            ),
        }
        for index in range(1, 15)
    ]
    state = bot.default_state()
    cache_epoch = bot.now_epoch()
    state["tweet_cache"] = {
        row["id"]: {**row, "text_is_complete": True, "cached_epoch": cache_epoch} for row in chain
    }
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    patch_tweet_lookup_method(monkeypatch, "fetch", lambda *_args, **_kwargs: pytest.fail(
            "the verified cached parent path should be sufficient"
        ))

    prepared_context = bot._reply_context_owner().build(
        mention,
        state,
    )
    assert prepared_context is not None
    context = prepared_context.context

    assert [turn["post_id"] for turn in context["visible_conversation"]] == [
        "1",
        *[str(index) for index in range(5, 16)],
    ]
    assert context["visible_conversation"][-1]["post_id"] == "15"
    assert sum(
        turn["post_id"] == "15" for turn in context["visible_conversation"]
    ) == 1


def test_uncached_parent_chain_performs_at_most_three_direct_lookups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound new X work without lowering the verified cached-path ceiling."""

    mention = {
        "id": "10",
        "author_id": "200",
        "text": "Target contribution.",
        "conversation_id": "1",
        "referenced_tweets": [{"type": "replied_to", "id": "9"}],
    }
    parents = {
        str(index): {
            "id": str(index),
            "author_id": "200",
            "text": f"Parent {index}.",
            "conversation_id": "1",
            "referenced_tweets": (
                [{"type": "replied_to", "id": str(index - 1)}]
                if index > 1
                else []
            ),
        }
        for index in range(1, 10)
    }
    lookups: list[str] = []

    def direct_lookup(tweet_id: str, *, include_media: bool = False) -> dict:
        assert include_media is False
        lookups.append(str(tweet_id))
        return copy.deepcopy(parents[str(tweet_id)])

    patch_tweet_lookup_method(monkeypatch, "fetch", direct_lookup)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    chain = bot._reply_context_owner().parent_chain(mention, bot.default_state())

    assert lookups == ["9", "8", "7"]
    assert [post["id"] for post in chain] == ["7", "8", "9"]


def test_parent_created_after_target_is_not_admitted_to_visible_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mention = {
        "id": "2",
        "author_id": "200",
        "text": "Target.",
        "conversation_id": "1",
        "created_at": "2026-09-04T12:00:00Z",
        "referenced_tweets": [{"type": "replied_to", "id": "1"}],
    }
    parent = {
        "id": "1",
        "author_id": "12345",
        "text": "Impossible later parent.",
        "conversation_id": "1",
        "created_at": "2026-09-04T12:01:00Z",
        "referenced_tweets": [],
    }
    patch_reply_context_method(monkeypatch, "parent_chain", lambda *_args: [parent])

    assert bot._reply_context_owner().build(mention, bot.default_state()) is None


def test_context_uses_only_parent_contiguous_path_not_cached_siblings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_epoch = bot.now_epoch()
    mention = {
        "id": "300",
        "author_id": "200",
        "conversation_id": "100",
        "text": "What follows from all that?",
        "referenced_tweets": [{"type": "replied_to", "id": "150"}],
    }
    state = bot.default_state()
    state["tweet_cache"] = {
        "100": {
            "id": "100", "text_is_complete": True, "cached_epoch": cache_epoch, "author_id": "12345", "conversation_id": "100",
            "text": "Opening post.", "referenced_tweets": [],
        },
        "150": {
            "id": "150", "text_is_complete": True, "cached_epoch": cache_epoch, "author_id": "200", "conversation_id": "100",
            "text": "Immediate capped parent.", "post_type": "author_cap_context",
            "referenced_tweets": [{"type": "replied_to", "id": "100"}],
        },
        "160": {
            "id": "160", "text_is_complete": True, "cached_epoch": cache_epoch, "author_id": "200", "conversation_id": "700",
            "text": "Older capped sibling.", "post_type": "author_cap_context",
            "referenced_tweets": [{"type": "replied_to", "id": "100"}],
        },
        "170": {
            "id": "170", "text_is_complete": True, "cached_epoch": cache_epoch, "author_id": "200", "conversation_id": "700",
            "text": "Newer capped sibling.", "post_type": "author_cap_context",
            "referenced_tweets": [{"type": "replied_to", "id": "100"}],
        },
        "180": {
            "id": "180", "text_is_complete": True, "cached_epoch": cache_epoch, "author_id": "200", "conversation_id": "700",
            "text": "Newest capped sibling.", "post_type": "author_cap_context",
            "referenced_tweets": [{"type": "replied_to", "id": "100"}],
        },
        "190": {
            "id": "190", "text_is_complete": True, "cached_epoch": cache_epoch, "author_id": "201", "conversation_id": "700",
            "text": "Other author.", "post_type": "author_cap_context",
            "referenced_tweets": [],
        },
        "200": {
            "id": "200", "text_is_complete": True, "cached_epoch": cache_epoch, "author_id": "200", "conversation_id": "701",
            "text": "Other conversation.", "post_type": "author_cap_context",
            "referenced_tweets": [],
        },
    }
    patch_tweet_lookup_method(monkeypatch, "fetch", lambda *_args, **_kwargs: pytest.fail("context must use tweet_cache"))

    prepared_context = bot._reply_context_owner().build(mention, state)
    assert prepared_context is not None
    context = prepared_context.context

    assert [post["post_id"] for post in context["parent_thread"]] == ["100", "150"]
    assert sum(post["post_id"] == "150" for post in context["parent_thread"]) == 1
    assert all(post["post_id"] not in {"190", "200"} for post in context["parent_thread"])


def test_author_cap_context_quote_commentary_refreshes_original_with_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    cache_epoch = bot.now_epoch()
    mention = {
        "id": "920",
        "author_id": "200",
        "conversation_id": "910",
        "text": "Can you answer beneath my quote?",
        "referenced_tweets": [{"type": "replied_to", "id": "910"}],
    }
    state = bot.default_state()
    state["tweet_cache"] = {
        "900": {
            "id": "900", "text_is_complete": True, "cached_epoch": cache_epoch, "author_id": "12345", "conversation_id": "900",
            "text": "The original account post.", "referenced_tweets": [],
        },
        "910": {
            "id": "910", "text_is_complete": True, "cached_epoch": cache_epoch, "author_id": "200", "conversation_id": "910",
            "text": "My capped quote commentary.", "post_type": "author_cap_quote_context",
            "referenced_tweets": [{"type": "quoted", "id": "900"}],
        },
    }
    lookups: list[tuple[str, bool]] = []

    def fetch(tweet_id: str, *, include_media: bool = False) -> dict:
        lookups.append((str(tweet_id), include_media))
        return {
            **copy.deepcopy(state["tweet_cache"]["900"]),
            "attachments": {"media_keys": ["photo-root"]},
            "_attached_media": [
                {
                    "media_key": "photo-root",
                    "type": "photo",
                    "url": "https://pbs.twimg.com/media/root.jpg",
                }
            ],
        }

    patch_tweet_lookup_method(monkeypatch, "fetch", fetch)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    prepared_context = bot._reply_context_owner().build(mention, state)
    assert prepared_context is not None
    context = prepared_context.context

    assert lookups == [("900", True)]
    assert context["parent_thread"] == [
        {"post_id": "910", "author_role": "user", "text": "My capped quote commentary."},
    ]
    assert context["quoted_post"] == {
        "post_id": "900", "author_role": "account", "text": "The original account post.",
    }
    assert context["quoted_post_relationship"] == "root_quote"
    assert prepared_context.media_context["photos"] == [
        {
            "media_key": "photo-root",
            "url": "https://pbs.twimg.com/media/root.jpg",
            "attachment_role": "quoted_subject",
            "source_post_id": "900",
        }
    ]
    payload, _fact_map = build_model_payload(
        context=context,
        repository=UNIT_REPLY_REPOSITORY,
    )
    assert payload["quoted_subject"] == {
        "relationship": "root_quote",
        "post_id": "900",
        "role": "account",
        "text": "The original account post.",
    }
    assert [
        turn["post_id"] for turn in payload["visible_conversation"]
    ] == ["910", "920"]


def test_declared_ancestor_quote_fails_context_closed_when_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not silently drop a structured quote declared by the root ancestor."""

    root = {
        "id": "910",
        "author_id": "200",
        "conversation_id": "910",
        "text": "Root commentary.",
        "referenced_tweets": [{"type": "quoted", "id": "900"}],
    }
    target = {
        "id": "920",
        "author_id": "200",
        "conversation_id": "910",
        "text": "Follow-up commentary.",
        "referenced_tweets": [{"type": "replied_to", "id": "910"}],
    }
    lookups: list[tuple[str, bool]] = []
    patch_reply_context_method(monkeypatch, "parent_chain", lambda *_args: [root])

    def missing(
        tweet_id: str,
        _state: dict,
        *,
        include_media: bool = False,
    ) -> None:
        lookups.append((str(tweet_id), include_media))
        return None

    patch_tweet_lookup_method(monkeypatch, "get_cached", missing)

    assert bot._reply_context_owner().build(target, bot.default_state()) is None
    assert lookups == [("900", True)]


def test_reply_plus_quote_preserves_real_thread_and_separates_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not replace a verified reply chain with a directly quoted branch."""

    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    root = {
        "id": "100",
        "author_id": "12345",
        "conversation_id": "100",
        "text": "The actual thread root.",
        "referenced_tweets": [],
    }
    parent = {
        "id": "200",
        "author_id": "201",
        "conversation_id": "100",
        "text": "The immediate parent.",
        "referenced_tweets": [{"type": "replied_to", "id": "100"}],
    }
    target = {
        "id": "300",
        "author_id": "202",
        "conversation_id": "100",
        "text": "My reply also quotes this.",
        "referenced_tweets": [
            {"type": "replied_to", "id": "200"},
            {"type": "quoted", "id": "900"},
        ],
    }
    quoted = {
        "id": "900",
        "author_id": "203",
        "conversation_id": "900",
        "text": "The separately quoted subject.",
        "referenced_tweets": [],
    }
    patch_reply_context_method(monkeypatch, "parent_chain", lambda *_args: [root, parent])
    patch_tweet_lookup_method(
        monkeypatch, "get_cached",
        lambda tweet_id, *_args, **_kwargs: (
            quoted
            if str(tweet_id) == "900"
            else pytest.fail("only the quoted subject may be fetched")
        ),
    )
    monkeypatch.setattr(
        bot,
        "reply_media_context_for_candidate",
        lambda *_args, **_kwargs: {},
    )

    prepared_context = bot._reply_context_owner().build(
        target,
        bot.default_state(),
    )
    assert prepared_context is not None
    context = prepared_context.context

    assert [
        turn["post_id"] for turn in context["visible_conversation"]
    ] == ["100", "200", "300"]
    assert context["root_post_id"] == "100"
    assert context["parent_post_id"] == "200"
    assert context["quoted_post"] == {
        "post_id": "900",
        "author_role": "other_user",
        "text": "The separately quoted subject.",
    }
    assert context["quoted_post_relationship"] == "target_quote"

    payload, _fact_map = build_model_payload(
        context=context,
        repository=UNIT_REPLY_REPOSITORY,
    )
    assert [
        turn["post_id"] for turn in payload["visible_conversation"]
    ] == ["100", "200", "300"]
    assert payload["quoted_subject"] == {
        "relationship": "target_quote",
        "post_id": "900",
        "role": "other_user",
        "text": "The separately quoted subject.",
    }


def test_direct_quote_refreshes_cache_without_replacing_the_reply_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    cache_epoch = bot.now_epoch()
    mention = {
        "id": "920",
        "author_id": "200",
        "conversation_id": "920",
        "text": "What do you make of this?",
        "referenced_tweets": [{"type": "quoted", "id": "900"}],
    }
    state = bot.default_state()
    state["tweet_cache"] = {
        "900": {
            "id": "900",
            "cached_epoch": cache_epoch,
            "author_id": "12345",
            "conversation_id": "900",
            "text": "The directly quoted account post.",
            "referenced_tweets": [],
        }
    }
    lookups: list[tuple[str, bool]] = []

    def fetch(tweet_id: str, *, include_media: bool = False) -> dict:
        lookups.append((tweet_id, include_media))
        return {
            "id": "900",
            "author_id": "12345",
            "conversation_id": "900",
            "text": "The directly quoted account post.",
            "referenced_tweets": [],
            "attachments": {"media_keys": ["photo-1"]},
            "_attached_media": [
                {
                    "media_key": "photo-1",
                    "type": "photo",
                    "url": "https://pbs.twimg.test/photo.jpg",
                }
            ],
        }

    patch_tweet_lookup_method(monkeypatch, "fetch", fetch)

    prepared_context = bot._reply_context_owner().build(mention, state)
    assert prepared_context is not None
    context = prepared_context.context

    assert lookups == [("900", True)]
    assert [
        turn["post_id"] for turn in context["visible_conversation"]
    ] == ["920"]
    assert context["quoted_post"]["text"] == "The directly quoted account post."
    assert context["quoted_post_relationship"] == "target_quote"
    assert context["root_post_id"] == "920"
    assert context["parent_post_id"] is None
    assert prepared_context.media_context["photos"] == [
        {
            "media_key": "photo-1",
            "url": "https://pbs.twimg.test/photo.jpg",
            "attachment_role": "quoted_subject",
            "source_post_id": "900",
        }
    ]


def test_image_only_direct_quote_reaches_one_multimodal_sol_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retain a verified quote identity even when the quoted post has no text."""

    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    target = {
        "id": "920",
        "author_id": "200",
        "conversation_id": "920",
        "text": "What do you make of this?",
        "referenced_tweets": [{"type": "quoted", "id": "900"}],
    }
    quoted = {
        "id": "900",
        "author_id": "201",
        "conversation_id": "900",
        "text": "",
        "referenced_tweets": [],
        "attachments": {"media_keys": ["photo-1"]},
        "_attached_media": [
            {
                "media_key": "photo-1",
                "type": "photo",
                "url": "https://pbs.twimg.test/photo.jpg",
            }
        ],
    }
    patch_tweet_lookup_method(
        monkeypatch, "get_cached",
        lambda tweet_id, *_args, **_kwargs: (
            quoted
            if str(tweet_id) == "900"
            else pytest.fail("only the quoted image post may be fetched")
        ),
    )

    prepared_context = bot._reply_context_owner().build(
        target,
        bot.default_state(),
    )
    assert prepared_context is not None
    context = prepared_context.context

    assert context["quoted_post"] is None
    assert context["quoted_post_id"] == "900"
    assert prepared_context.media_context["photos"] == [
        {
            "media_key": "photo-1",
            "url": "https://pbs.twimg.test/photo.jpg",
            "attachment_role": "quoted_subject",
            "source_post_id": "900",
        }
    ]
    calls: list[dict[str, object]] = []
    result = run_single_call_reply_pipeline(
        context=context,
        config={
            "enabled": True,
            "strategy_version": STRATEGY_VERSION,
            "model": "gpt-5.6-sol",
            "timeout_seconds": 180,
        },
        repository=UNIT_REPLY_REPOSITORY,
        transport=lambda **kwargs: (
            calls.append(kwargs)
            or {
                "response": {
                    "id": "resp_image_only_quote",
                    "status": "completed",
                    "model": "gpt-5.6-sol",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps(
                                        {
                                            "decision": "reply",
                                            "reply_kind": "principle",
                                            "reply": "Judgment matters more than appearances.",
                                            "factual_claims": [],
                                            "used_fact_ids": [],
                                            "reason_code": "useful_reply",
                                        },
                                    ),
                                }
                            ],
                        }
                    ],
                    "usage": {},
                }
            }
        ),
        supplied_images=[
            {
                "identity": "photo-1",
                "mime_type": "image/jpeg",
                "data": __import__("tests.helpers.single_call_fixtures", fromlist=["valid_jpeg"]).valid_jpeg(),
                "attachment_role": "quoted_subject",
                "source_post_id": "900",
            }
        ],
    )
    assert result.status == "reply"
    assert len(calls) == 1
    content = calls[0]["request"]["input"][0]["content"]
    assert [item["type"] for item in content] == ["input_text", "input_image"]
    payload = json.loads(content[0]["text"])
    assert payload["quoted_subject"] is None
    assert payload["identities"]["subject_post_id"] == "900"
    assert payload["supplied_images"] == [
        {
            "attachment_role": "quoted_subject",
            "image_index": 1,
            "source_post_id": "900",
        }
    ]


def test_non_contiguous_cached_author_cap_context_is_not_invented_into_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    cache_epoch = bot.now_epoch()
    mention = {
        "id": "960",
        "author_id": "200",
        "conversation_id": "910",
        "text": "A newer contribution in the same conversation.",
        "referenced_tweets": [],
    }
    state = bot.default_state()
    state["tweet_cache"] = {
        "900": {
            "id": "900", "cached_epoch": cache_epoch, "author_id": "12345",
            "conversation_id": "900", "text": "The original account post.",
            "referenced_tweets": [],
        },
        "910": {
            "id": "910", "cached_epoch": cache_epoch, "author_id": "200",
            "conversation_id": "910", "text": "Older capped quote commentary.",
            "post_type": "author_cap_quote_context",
            "referenced_tweets": [{"type": "quoted", "id": "900"}],
        },
        **{
            str(tweet_id): {
                "id": str(tweet_id), "cached_epoch": cache_epoch,
                "author_id": "200", "conversation_id": "910",
                "text": f"Newer capped context {tweet_id}.",
                "post_type": "author_cap_context", "referenced_tweets": [],
            }
            for tweet_id in (920, 930, 940, 950)
        },
    }
    patch_tweet_lookup_method(monkeypatch, "fetch", lambda *_args, **_kwargs: pytest.fail("cached cap context must not fetch from X"))

    prepared_context = bot._reply_context_owner().build(mention, state)
    assert prepared_context is not None
    context = prepared_context.context

    assert context["parent_thread"] == []
    assert context["visible_conversation"] == [
        {
            "post_id": "960",
            "author_role": "user",
            "text": "A newer contribution in the same conversation.",
        }
    ]
    assert context["quoted_post"] is None


def test_trim_context_text_never_exceeds_requested_limit() -> None:
    for maximum in (0, 1, 2, 3, 20):
        assert len(bot.trim_context_text("ordinary words " * 20, maximum)) <= maximum


def test_quote_tweet_context_never_truncates_away_user_commentary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incoming = "INCOMING-QUOTE-MARKER courage and responsibility"
    monkeypatch.setattr(bot, "THREAD_CONTEXT_MAX_TOTAL_CHARS", 220)
    monkeypatch.setattr(bot, "THREAD_CONTEXT_MAX_CHARS_PER_POST", 500)

    prepared_context = bot._reply_context_owner().build_quote(
        {"id": "900", "text": "original account post " + ("historical context " * 80)},
        {"id": "910", "conversation_id": "910", "author_id": "200", "text": incoming},
    )
    assert prepared_context is not None
    context = prepared_context.context

    assert context["incoming_contribution"] == incoming
    assert context["quoted_post"]["post_id"] == "900"
    assert context["visible_conversation"][-1]["text"] == incoming
    assert sum(
        len(turn["text"]) for turn in context["visible_conversation"]
    ) <= bot.MAX_VISIBLE_TEXT_CHARACTERS


def test_meme_summary_context_limit_covers_current_analysis_shape() -> None:
    summary = (
        "2x2 grid meme: top-left Cuba 2016 rundown street, bottom-left Venezuela 2019 street scene with man on rubble, "
        "bottom cartoon 'Fantasy Land' candy castle; right column shows same man saying 'I PREFER REAL SOCIALISM', "
        "'I SAID REAL SOCIALISM', then 'PERFECTION' over the fantasy image Anti-socialist message: Real-world socialism "
        "produces poverty and failure; the only 'real socialism' that works is pure fantasy Analysis metadata: ranking 17, "
        "shareability high."
    )
    context = bot.tweet_context_text({"image_summary": summary})

    assert len(context) <= bot.THREAD_CONTEXT_MAX_CHARS_PER_POST
    assert bot.trim_context_text(context, bot.THREAD_CONTEXT_MAX_CHARS_PER_POST) == context

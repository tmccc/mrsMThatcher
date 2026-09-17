"""Regression tests for bot state storage."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime


pytestmark = pytest.mark.allow_loopback_network


@pytest.mark.parametrize(
    ("key", "bad_value"),
    [
        ("daily_reply_count", 1.9),
        ("last_reply_epoch", 1_800_000_000.5),
        ("daily_reply_count", float("inf")),
    ],
)
def test_state_rejects_fractional_and_non_finite_numbers(
    tmp_path: Path,
    key: str,
    bad_value: float,
) -> None:
    state = bot.default_state()
    state[key] = bad_value

    assert bot.normalise_state_candidate(state, path=tmp_path / "state.json") is None


def test_load_state_rejects_absurd_epoch_primary_and_recovers_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    bot.atomic_write_json(
        state_file,
        {
            "next_meme_post_epoch": 10**30,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": "9999-01-01",
            "meme_anchor_quote_post_epoch": 0,
        },
    )
    bot.atomic_write_json(
        tmp_path / "bot_state.json.bak1",
        {
            "replied_to_ids": ["from-bak1"],
            "next_quote_post_epoch": 1_800_000_000,
        },
    )

    recovered = bot.load_state()

    assert recovered["replied_to_ids"] == ["from-bak1"]
    assert recovered["next_quote_post_epoch"] == 1_800_000_000


def test_load_state_refuses_valid_primary_latest_backup_divergence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two valid latest-generation candidates cannot be ordered by pathname."""

    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    bot.atomic_write_json(
        state_file,
        {
            "replied_to_ids": [],
            "last_reply_epoch": 1_800_000_000,
            "next_quote_post_epoch": 1_800_001_000,
        },
    )
    bot.atomic_write_json(
        tmp_path / "bot_state.json.bak1",
        {
            "replied_to_ids": ["950001"],
            "last_reply_epoch": 1_800_000_100,
            "next_quote_post_epoch": 1_800_002_000,
        },
    )

    with pytest.raises(
        RuntimeError,
        match="Primary state and latest committed backup.*diverge",
    ):
        bot.load_state()


def test_load_state_accepts_matching_primary_and_latest_backup_after_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    state = bot.default_state()
    state["last_reply_epoch"] = 1_800_000_100

    bot.save_state(state, durable=True)

    assert state_file.read_bytes() == (tmp_path / "bot_state.json.bak1").read_bytes()
    assert bot.load_state()["last_reply_epoch"] == 1_800_000_100


def test_load_state_accepts_semantically_equal_differently_encoded_latest_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON layout differences do not constitute state-generation divergence."""

    state_file = tmp_path / "bot_state.json"
    backup_file = tmp_path / "bot_state.json.bak1"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    state = bot.default_state()
    state["last_reply_epoch"] = 1_800_000_100
    persisted = bot.state_document_for_persistence(state)
    bot.atomic_write_json(state_file, persisted)
    reordered = dict(reversed(list(persisted.items())))
    backup_file.write_text(
        json.dumps(reordered, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    backup_file.chmod(0o600)

    assert state_file.read_bytes() != backup_file.read_bytes()
    assert bot.load_state()["last_reply_epoch"] == 1_800_000_100


def test_load_state_does_not_let_stale_older_backup_veto_usable_latest_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 3)
    state = bot.default_state()
    state["last_reply_epoch"] = 1_800_000_100
    bot.save_state(state, durable=True)
    bot.atomic_write_json(
        tmp_path / "bot_state.json.bak2",
        {
            "pending_reply_drafts": {
                "mention:100": {"reply_text": "obsolete draft"},
            }
        },
    )

    assert bot.load_state()["last_reply_epoch"] == 1_800_000_100


def test_load_state_ignores_stale_backup_when_backups_are_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disabled backup generation is not part of the recovery authority."""

    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    bot.atomic_write_json(
        state_file,
        {
            "last_reply_epoch": 1_800_000_100,
            "next_quote_post_epoch": 1_800_001_000,
        },
    )
    bot.atomic_write_json(
        tmp_path / "bot_state.json.bak1",
        {
            "last_reply_epoch": 1_800_000_000,
            "next_quote_post_epoch": 1_800_000_500,
        },
    )

    recovered = bot.load_state()

    assert recovered["last_reply_epoch"] == 1_800_000_100
    assert recovered["next_quote_post_epoch"] == 1_800_001_000


def test_load_state_rejects_malformed_tweet_cache_epoch_and_recovers_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    bot.atomic_write_json(
        state_file,
        {"tweet_cache": {"123": {"cached_epoch": "banana"}}},
    )
    bot.atomic_write_json(
        tmp_path / "bot_state.json.bak1",
        {"replied_to_ids": ["from-bak1"], "tweet_cache": {}},
    )

    recovered = bot.load_state()

    assert recovered["replied_to_ids"] == ["from-bak1"]
    assert recovered["tweet_cache"] == {}


@pytest.mark.parametrize(
    "cache_entry",
    [
        {"referenced_tweets": "banana"},
        {"referenced_tweets": ["banana"]},
    ],
)
def test_load_state_rejects_malformed_tweet_cache_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cache_entry: dict,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    bot.atomic_write_json(state_file, {"tweet_cache": {"123": cache_entry}})
    bot.atomic_write_json(tmp_path / "bot_state.json.bak1", {"tweet_cache": {"456": {"cached_epoch": 1_800_000_000}}})

    recovered = bot.load_state()

    assert "123" not in recovered["tweet_cache"]
    assert recovered["tweet_cache"]["456"]["cached_epoch"] == 1_800_000_000


def test_tweet_cache_normalisation_stringifies_scalars_and_preserves_valid_context(tmp_path: Path) -> None:
    state = {
        "tweet_cache": {
            123: {
                "id": 123,
                "author_id": 456,
                "conversation_id": 789,
                "text": 12345,
                "image_summary": 67890,
                "created_at": 111,
                "post_type": 222,
                "cached_epoch": "1800000000",
                "referenced_tweets": [{"type": 333, "id": 444, "extra": 555}],
            }
        }
    }

    normalised = bot.normalise_state_candidate(state, path=tmp_path / "bot_state.json")

    assert normalised is not None
    entry = normalised["tweet_cache"]["123"]
    assert entry["id"] == "123"
    assert entry["author_id"] == "456"
    assert entry["conversation_id"] == "789"
    assert entry["text"] == "12345"
    assert entry["image_summary"] == "67890"
    assert entry["created_at"] == "111"
    assert entry["post_type"] == "222"
    assert entry["cached_epoch"] == 1_800_000_000
    assert entry["referenced_tweets"] == [{"type": "333", "id": "444", "extra": "555"}]


def test_valid_tweet_cache_round_trips_through_state_loading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    bot.atomic_write_json(
        state_file,
        {
            "tweet_cache": {
                "123": {
                    "id": "123",
                    "author_id": "456",
                    "conversation_id": "789",
                    "text": "Useful cached context",
                    "image_summary": "A useful image summary",
                    "created_at": "2026-07-06T10:00:00",
                    "post_type": "quote",
                    "cached_epoch": 1_800_000_000,
                    "referenced_tweets": [{"type": "replied_to", "id": "111"}],
                }
            }
        },
    )

    recovered = bot.load_state()

    assert recovered["tweet_cache"]["123"] == {
        "id": "123",
        "author_id": "456",
        "conversation_id": "789",
        "text": "Useful cached context",
        "image_summary": "A useful image summary",
        "created_at": "2026-07-06T10:00:00",
        "post_type": "quote",
        "cached_epoch": 1_800_000_000,
        "referenced_tweets": [{"type": "replied_to", "id": "111"}],
    }


def test_cache_tweet_uses_fake_clock_for_generated_created_at(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_epoch = 1_800_000_000
    state = bot.default_state()
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)

    cached = bot.cache_tweet(
        state,
        tweet_id="123",
        text="hello",
        author_id="456",
    )

    assert cached["cached_epoch"] == fixed_epoch
    assert cached["created_at"] == datetime.fromtimestamp(fixed_epoch).isoformat()


def test_cache_tweet_normalises_safe_scalar_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_epoch = 1_800_000_000
    state = bot.default_state()
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)

    cached = bot.cache_tweet(
        state,
        tweet_id=123,
        text=["not", "a", "string"],
        author_id=456,
        conversation_id=789,
        referenced_tweets=[{"type": 1, "id": 2}],
        created_at=111,
        image_summary=222,
        post_type=333,
    )

    assert cached == {
        "id": "123",
        "author_id": "456",
        "conversation_id": "789",
        "created_at": "111",
        "referenced_tweets": [{"type": "1", "id": "2"}],
        "text": "['not', 'a', 'string']",
        "text_is_complete": True, "cached_epoch": fixed_epoch,
        "image_summary": "222",
        "post_type": "333",
    }


@pytest.mark.parametrize("referenced_tweets", ["banana", ["banana"]])
def test_cache_tweet_rejects_malformed_referenced_tweets(referenced_tweets: object) -> None:
    state = bot.default_state()

    with pytest.raises(ValueError):
        bot.cache_tweet(
            state,
            tweet_id="123",
            text="hello",
            author_id="456",
            referenced_tweets=referenced_tweets,
        )

    assert state["tweet_cache"] == {}


def test_get_immediate_parent_id_accepts_absent_or_valid_references() -> None:
    assert bot.get_immediate_parent_id({}) is None
    assert bot.get_immediate_parent_id({"referenced_tweets": None}) is None
    assert bot.get_immediate_parent_id({
        "referenced_tweets": [{"type": "quoted", "id": "111"},
                              {"type": "replied_to", "id": 222}],
    }) == "222"


@pytest.mark.parametrize(
    "referenced_tweets",
    ["banana", ["banana"], [{"type": "replied_to"}],
     [{"type": "replied_to", "id": "not-a-tweet-id"}]],
)
def test_get_immediate_parent_id_rejects_malformed_api_references(
    referenced_tweets: object,
) -> None:
    with pytest.raises(bot.ApiError, match="malformed referenced_tweets"):
        bot.get_immediate_parent_id({"referenced_tweets": referenced_tweets})


def test_load_state_rejects_malformed_last_seen_mention_id_and_recovers_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    bot.atomic_write_json(state_file, {"last_seen_mention_id": []})
    bot.atomic_write_json(tmp_path / "bot_state.json.bak1", {"last_seen_mention_id": "123"})

    recovered = bot.load_state()

    assert recovered["last_seen_mention_id"] == "123"


def test_load_state_normalises_optional_scalar_ids(tmp_path: Path) -> None:
    state = {
        "last_seen_mention_id": 123,
        "last_main_post_id": 456,
        "last_regular_image_filename": 789,
    }

    normalised = bot.normalise_state_candidate(state, path=tmp_path / "bot_state.json")

    assert normalised is not None
    assert normalised["last_seen_mention_id"] == "123"
    assert normalised["last_main_post_id"] == "456"
    assert normalised["last_regular_image_filename"] == "789"


def test_mention_pagination_state_canonicalises_legacy_empty_base(
    tmp_path: Path,
) -> None:
    normalised = bot.normalise_mention_pagination(
        {"next_token": "page-4"},
        path=tmp_path / "bot_state.json",
    )

    assert normalised == {
        "base_since_id": "",
        "next_token": "page-4",
    }


@pytest.mark.parametrize(
    "value",
    [
        {"base_since_id": "99"},
        {"base_since_id": [], "next_token": "page-4"},
        {"base_since_id": "99", "next_token": {"nested": "bad"}},
        {
            "base_since_id": "99",
            "next_token": "page-4",
            "unexpected": True,
        },
    ],
)
def test_mention_pagination_state_rejects_malformed_cursor(
    tmp_path: Path,
    value: object,
) -> None:
    assert bot.normalise_mention_pagination(
        value,
        path=tmp_path / "bot_state.json",
    ) is None


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"base_since_id": "99"},
        {"base_since_id": 99, "head_traversal_started": False},
        {"base_since_id": "99", "head_traversal_started": 0},
        {
            "base_since_id": "99",
            "head_traversal_started": False,
            "unexpected": True,
        },
    ],
)
def test_mention_backlog_reset_guard_rejects_malformed_state(
    tmp_path: Path,
    value: object,
) -> None:
    assert bot.normalise_mention_backlog_reset_guard(
        value,
        path=tmp_path / "bot_state.json",
    ) is None


def test_state_backup_bak1_contains_newest_committed_meme_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 5)
    bot.save_state({"posted_meme_filenames": [], "next_meme_post_epoch": 1_700_000_000}, durable=True)
    committed = {
        "posted_meme_filenames": ["001_meme.png"],
        "last_main_post_id": "970001",
        "last_meme_post_epoch": 1_800_000_000,
        "next_meme_post_epoch": 1_800_086_400,
    }
    bot.save_state(committed, durable=True)
    state_file.write_text("{bad json", encoding="utf-8")

    recovered = bot.load_state()

    assert recovered["posted_meme_filenames"] == ["001_meme.png"]
    assert recovered["last_main_post_id"] == "970001"
    assert recovered["next_meme_post_epoch"] == 1_800_086_400


def test_state_backup_bak1_contains_newest_committed_regular_quote_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 5)
    bot.save_state({"next_quote_post_epoch": 1}, durable=True)
    committed = {
        "last_main_post_id": "950001",
        "last_quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
    }
    bot.save_state(committed, durable=True)
    state_file.write_text("{bad json", encoding="utf-8")

    recovered = bot.load_state()

    assert recovered["last_main_post_id"] == "950001"
    assert recovered["last_quote_post_epoch"] == 1_800_000_000
    assert recovered["next_quote_post_epoch"] == 1_800_007_200


def test_protected_durable_write_fails_when_parent_fsync_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "protected.json"
    real_open = bot.os.open

    def failing_open(path: str, flags: int, mode: int = 0o777) -> int:
        if Path(path) == tmp_path:
            raise OSError("directory fsync unavailable")
        return real_open(path, flags, mode)

    monkeypatch.setattr(bot.os, "open", failing_open)

    with pytest.raises(OSError, match="directory fsync unavailable"):
        bot.atomic_write_json(target, {"ok": True}, durable=True)


def test_append_unique_capped_preserves_order_and_moves_existing_item_to_tail() -> None:
    assert bot.append_unique_capped(["a", "b", "c"], "b", 3) == ["a", "c", "b"]


def test_append_unique_capped_discards_oldest_items() -> None:
    assert bot.append_unique_capped(["a", "b", "c"], "d", 3) == ["b", "c", "d"]

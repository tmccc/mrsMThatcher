"""Regression coverage for durable runtime-state reader compatibility."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import mrsMThatcher2 as bot
from mrs_bot_state_generation import encode_generation


def test_new_state_fences_precompatibility_reader_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["future_extension"] = {"preserved": True}

    bot.save_state(state, durable=True)

    before = state_file.read_bytes()
    persisted = json.loads(before)
    assert (
        persisted["minimum_reader_version"]
        == bot.STATE_MINIMUM_READER_VERSION
    )
    assert (
        persisted["pending_reply_drafts"]
        == bot.STATE_READER_COMPATIBILITY_FENCE
    )

    def simulated_precompatibility_reader(payload: bytes) -> dict:
        candidate = json.loads(payload)
        legacy_drafts = candidate.get("pending_reply_drafts")
        if legacy_drafts not in (None, {}):
            raise RuntimeError("Legacy V1 reply drafts remain")
        candidate["last_seen_mention_id"] = "105"
        return candidate

    with pytest.raises(RuntimeError, match="Legacy V1 reply drafts remain"):
        simulated_precompatibility_reader(before)
    with pytest.raises(
        bot.IncompatibleStateReaderError,
        match="minimum reader version",
    ):
        bot.require_compatible_state_reader(
            persisted,
            path=state_file,
            reader_version=bot.STATE_MINIMUM_READER_VERSION - 1,
        )
    assert bot.require_compatible_state_reader(
        persisted,
        path=state_file,
        reader_version=bot.STATE_READER_VERSION,
    ) == bot.STATE_MINIMUM_READER_VERSION
    assert bot.require_compatible_state_reader(
        persisted,
        path=state_file,
        reader_version=bot.STATE_READER_VERSION + 1,
    ) == bot.STATE_MINIMUM_READER_VERSION

    assert state_file.read_bytes() == before
    loaded = bot.load_state()
    assert loaded["minimum_reader_version"] == bot.STATE_MINIMUM_READER_VERSION
    assert "pending_reply_drafts" not in loaded
    assert loaded["last_seen_mention_id"] == "99"
    assert loaded["future_extension"] == {"preserved": True}


def test_legacy_state_upgrades_and_reloads_through_all_backup_generations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 5)
    bot.atomic_write_json(
        state_file,
        {
            "last_seen_mention_id": "99",
            "future_extension": {"preserved": [1, 2, 3]},
        },
    )

    state = bot.load_state()
    for generation in range(bot.STATE_BACKUP_COUNT + 2):
        assert state["minimum_reader_version"] == bot.STATE_MINIMUM_READER_VERSION
        assert state["last_seen_mention_id"] == "99"
        assert state["future_extension"] == {"preserved": [1, 2, 3]}
        state["compatibility_generation"] = generation
        bot.save_state(state, durable=True)
        state = bot.load_state()
        assert state["compatibility_generation"] == generation

    for candidate in [
        state_file,
        *(state_file.with_name(f"{state_file.name}.bak{index}") for index in range(1, 6)),
    ]:
        persisted = json.loads(candidate.read_bytes())
        assert persisted["minimum_reader_version"] == bot.STATE_MINIMUM_READER_VERSION
        assert persisted["pending_reply_drafts"] == bot.STATE_READER_COMPATIBILITY_FENCE


def test_previous_reader_fence_upgrades_to_current_without_state_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    bot.atomic_write_json(
        state_file,
        {
            "minimum_reader_version": 2,
            "pending_reply_drafts": {
                "__mrs_state_reader_compatibility_fence__": 2,
            },
            "last_seen_mention_id": "99",
            "future_extension": {"preserved": True},
        },
    )

    loaded = bot.load_state()

    assert loaded["minimum_reader_version"] == bot.STATE_MINIMUM_READER_VERSION
    assert "pending_reply_drafts" not in loaded
    assert loaded["last_seen_mention_id"] == "99"
    assert loaded["future_extension"] == {"preserved": True}

    bot.save_state(loaded, durable=True)
    persisted = json.loads(state_file.read_bytes())
    assert persisted["minimum_reader_version"] == bot.STATE_MINIMUM_READER_VERSION
    assert persisted["pending_reply_drafts"] == bot.STATE_READER_COMPATIBILITY_FENCE
    with pytest.raises(bot.IncompatibleStateReaderError):
        bot.require_compatible_state_reader(
            persisted,
            path=state_file,
            reader_version=3,
        )


def test_future_state_rejects_before_compatible_backup_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    bot.save_state(state, durable=True)
    compatible_backup = state_file.with_name("bot_state.json.bak1").read_bytes()

    future = json.loads(state_file.read_bytes())
    future["minimum_reader_version"] = bot.STATE_READER_VERSION + 1
    future, _ = encode_generation(future, future["_state_generation"]["sequence"], bot.DURABLE_RUNTIME_JSON_MAX_BYTES)
    bot.atomic_write_json(state_file, future)

    with pytest.raises(
        bot.IncompatibleStateReaderError,
        match="minimum reader version",
    ):
        bot.load_state()

    assert state_file.with_name("bot_state.json.bak1").read_bytes() == compatible_backup


@pytest.mark.parametrize(
    "pending_reply_drafts",
    [None, {}, {"not_the_reserved_fence": 2}],
)
def test_current_version_state_requires_exact_rollback_fence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pending_reply_drafts: object,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    candidate = {
        "minimum_reader_version": bot.STATE_MINIMUM_READER_VERSION,
    }
    if pending_reply_drafts is not None:
        candidate["pending_reply_drafts"] = pending_reply_drafts
    candidate, _ = encode_generation(candidate, 1, bot.DURABLE_RUNTIME_JSON_MAX_BYTES)
    bot.atomic_write_json(state_file, candidate)

    with pytest.raises(RuntimeError, match="compatibility fence"):
        bot.load_state()

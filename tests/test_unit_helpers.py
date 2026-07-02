from __future__ import annotations

import json
import os
import pickle
import tempfile
from pathlib import Path

import pytest


UNIT_BASE = Path(tempfile.gettempdir()) / "mrsMThatcher-unit-import"
UNIT_BASE.mkdir(parents=True, exist_ok=True)

IMPORT_ENV = {
    "MRS_TEST_MODE": "1",
    "MRS_BASE_DIR": str(UNIT_BASE),
    "MRS_LOG_FILE": str(UNIT_BASE / "unit-test.log"),
    "X_API_BASE_URL": "http://127.0.0.1:9",
    "X_UPLOAD_BASE_URL": "http://127.0.0.1:9",
    "XAI_API_BASE_URL": "http://127.0.0.1:9/v1",
    "X_CONSUMER_KEY": "dummy",
    "X_CONSUMER_SECRET": "dummy",
    "X_ACCESS_TOKEN": "dummy",
    "X_ACCESS_SECRET": "dummy",
    "X_MY_USER_ID": "12345",
    "XAI_API_KEY": "dummy",
    "X_BEARER_TOKEN": "dummy",
}
ORIGINAL_ENV = {key: os.environ.get(key) for key in IMPORT_ENV}
os.environ.update(IMPORT_ENV)

import mrsMThatcher2 as bot  # noqa: E402

for key, value in ORIGINAL_ENV.items():
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


def test_used_history_json_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "used.json"

    bot.save_used_set(path, {3, 1, 2})

    assert json.loads(path.read_text(encoding="utf-8")) == [1, 2, 3]
    assert bot.load_used_set(path) == {1, 2, 3}


def test_used_history_migrates_from_legacy_pickle_when_json_missing(tmp_path: Path) -> None:
    json_path = tmp_path / "used.json"
    pickle_path = tmp_path / "used.pickle"
    with open(pickle_path, "wb") as f:
        pickle.dump({4, 5}, f)

    assert bot.load_used_set(json_path, legacy_pickle_path=pickle_path) == {4, 5}


def test_used_history_bad_json_falls_back_to_legacy_pickle(tmp_path: Path) -> None:
    json_path = tmp_path / "used.json"
    pickle_path = tmp_path / "used.pickle"
    json_path.write_text("{bad json", encoding="utf-8")
    with open(pickle_path, "wb") as f:
        pickle.dump([6, 7], f)

    assert bot.load_used_set(json_path, legacy_pickle_path=pickle_path) == {6, 7}


def test_used_history_missing_or_corrupt_files_return_empty_set(tmp_path: Path) -> None:
    json_path = tmp_path / "missing.json"
    pickle_path = tmp_path / "bad.pickle"
    pickle_path.write_bytes(b"not a pickle")

    assert bot.load_used_set(json_path) == set()
    assert bot.load_used_set(json_path, legacy_pickle_path=pickle_path) == set()


def test_choose_unused_line_returns_non_empty_line_and_marks_empty_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines_file = tmp_path / "mrsMThatcher.txt"
    lines_file.write_text("\nA usable quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot.random, "shuffle", lambda values: values.sort())

    used: set[int] = set()
    line_no, text = bot.choose_unused_line(used)

    assert (line_no, text) == (1, "A usable quote.")
    assert used == {0}


def test_choose_unused_line_resets_when_all_lines_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines_file = tmp_path / "mrsMThatcher.txt"
    lines_file.write_text("Only quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)

    used = {0}
    line_no, text = bot.choose_unused_line(used)

    assert (line_no, text) == (0, "Only quote.")
    assert used == set()


def test_choose_unused_image_resets_when_all_images_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))

    used = {0}
    image_no, selected = bot.choose_unused_image(used)

    assert image_no == 0
    assert selected == str(image_path)
    assert used == set()


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("", False),
        ("Too few", False),
        ("SKIP", False),
        ("A plain reply with enough length.", True),
        ("As Margaret Thatcher, I must respond.", False),
        ("I am Margaret Thatcher and I approve.", False),
        ("margaret thatcher said exactly this.", False),
    ],
)
def test_generated_reply_is_safe_enough(reply: str, expected: bool) -> None:
    assert bot.generated_reply_is_safe_enough(reply) is expected


def test_schedule_next_quote_post_uses_configured_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    state: dict = {}
    monkeypatch.setattr(bot.random, "randint", lambda low, high: 123)
    monkeypatch.setattr(bot, "save_state", lambda state: None)

    bot.schedule_next_quote_post(state, from_epoch=1_000)

    assert state["next_quote_post_epoch"] == 1_123


def test_local_config_coercion_accepts_boolean_strings_and_rejects_boolean_ints() -> None:
    assert bot._coerce_local_config_value("ENABLE_AUTO_REPLIES", "false", True) is False
    assert bot._coerce_local_config_value("ENABLE_AUTO_REPLIES", "yes", False) is True

    with pytest.raises(ValueError):
        bot._coerce_local_config_value("MAX_AUTO_REPLIES_PER_DAY", True, 24)


def test_local_config_coercion_rejects_negative_and_nonpositive_timings() -> None:
    with pytest.raises(ValueError):
        bot._coerce_local_config_value("POST_SLEEP_MIN", -1, bot.POST_SLEEP_MIN)

    with pytest.raises(ValueError):
        bot._coerce_local_config_value("MAX_AUTO_REPLIES_PER_DAY", 0, bot.MAX_AUTO_REPLIES_PER_DAY)

from __future__ import annotations

import json
import hashlib
import logging
import os
import tempfile
from datetime import datetime
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

from tests.fake_api_server import FakeApiServer, load_scenario  # noqa: E402

SCENARIOS = Path(__file__).resolve().parent / "fixtures" / "scenarios"


@pytest.fixture(autouse=True)
def isolate_regular_post_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Operational command tests model the supported post-bootstrap dispatch path.
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", tmp_path / "regular_post_receipt.json")
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", tmp_path / "meme_post_receipt.json")
    monkeypatch.setattr(bot, "CONFIRMED_REPLY_RECEIPT_FILE", tmp_path / "confirmed_reply_receipt.json")
    monkeypatch.setattr(bot, "AMBIGUOUS_POST_OUTCOME_FILE", tmp_path / "ambiguous_post_outcome.json")
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)


def quote_analysis_for_lines(lines: list[str], analyses: dict[int, dict] | None = None) -> dict:
    analyses = analyses or {}
    items: dict[str, dict] = {}
    line_index: dict[str, str] = {}
    for idx, text in enumerate(lines):
        if not text.strip():
            continue
        quote_hash = bot.quote_text_hash(text)
        line_index[str(idx + 1)] = quote_hash
        items.setdefault(
            quote_hash,
            {
                "text": bot.collapse_quote_whitespace(text),
                "quote_hash": quote_hash,
                "line_numbers": [idx + 1],
                "analysis": analyses.get(idx, {"seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"}}),
            },
        )
    return {
        "analysis_kind": "quotes",
        "schema_version": 2,
        "source": {"source_sha256": hashlib.sha256(("".join(line + "\n" for line in lines)).encode("utf-8")).hexdigest()},
        "line_index": line_index,
        "items": items,
    }


def xai_user_content(server: FakeApiServer) -> str | list[dict]:
    assert server.xai_requests
    return server.xai_requests[-1]["messages"][1]["content"]


def xai_image_urls(server: FakeApiServer) -> list[str]:
    content = xai_user_content(server)
    if not isinstance(content, list):
        return []
    return [
        part["image_url"]["url"]
        for part in content
        if part.get("type") == "image_url"
    ]


def fake_xai_response(status_code: int, body: dict | str) -> bot.requests.Response:
    response = bot.requests.Response()
    response.status_code = status_code
    if isinstance(body, str):
        response._content = body.encode("utf-8")
    else:
        response._content = json.dumps(body).encode("utf-8")
        response.headers["Content-Type"] = "application/json"
    return response


def run_native_photo_mention_with_xai_responses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    responses: list[dict],
) -> tuple[str, list[dict], list[dict], dict]:
    scenario = {
        "mentions": [
            {
                "id": "100",
                "text": "This depends on the image. https://t.co/example",
                "author_id": "200",
                "conversation_id": "100",
                "created_at": "2026-07-06T10:00:00Z",
                "attachments": {"media_keys": ["3_100"]},
            }
        ],
        "mentions_extra": {
            "includes": {
                "media": [
                    {
                        "media_key": "3_100",
                        "type": "photo",
                        "url": "https://pbs.twimg.com/media/native-photo.jpg",
                    }
                ]
            }
        },
        "xai_responses": responses,
    }
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")
        status = bot.maybe_reply_to_mentions(state)
        xai_posts = [request for request in server.requests if request["path"] == "/v1/chat/completions"]
        return status, xai_posts, list(server.xai_requests), state
    finally:
        server.stop()


def image_analysis_for_paths(paths: list[Path], analyses: dict[str, dict] | None = None) -> dict:
    analyses = analyses or {}
    path_index: dict[str, str] = {}
    items: dict[str, dict] = {}
    for path in paths:
        image_hash = bot.file_sha256(path)
        path_index[path.name] = image_hash
        items[image_hash] = {
            "paths": [path.name],
            "image_hash": image_hash,
            "analysis": analyses.get(path.name, {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}}),
        }
    return {"analysis_kind": "images", "schema_version": 3, "path_index": path_index, "items": items}


def write_image_analysis(path: Path, analysis: dict) -> None:
    path.write_text(json.dumps(analysis), encoding="utf-8")


def test_used_history_json_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "used.json"

    bot.save_used_set(path, {3, 1, 2, 11})

    assert json.loads(path.read_text(encoding="utf-8")) == [1, 2, 3, 11]
    assert bot.load_used_set(path) == {1, 2, 3, 11}


def test_used_history_refuses_legacy_pickle_when_json_missing(tmp_path: Path) -> None:
    json_path = tmp_path / "used.json"
    pickle_path = tmp_path / "used.pickle"
    pickle_path.write_bytes(b"legacy pickle no longer trusted")

    with pytest.raises(bot.CorruptUsedHistoryError):
        bot.load_used_set(json_path, legacy_pickle_path=pickle_path)
    assert not json_path.exists()


def test_used_history_bad_json_with_legacy_pickle_fails_closed(tmp_path: Path) -> None:
    json_path = tmp_path / "used.json"
    pickle_path = tmp_path / "used.pickle"
    json_path.write_text("{bad json", encoding="utf-8")
    pickle_path.write_bytes(b"legacy pickle no longer trusted")

    with pytest.raises(bot.CorruptUsedHistoryError):
        bot.load_used_set(json_path, legacy_pickle_path=pickle_path)


def test_used_history_json_order_is_normalized_on_load(tmp_path: Path) -> None:
    json_path = tmp_path / "used.json"
    json_path.write_text("[11, 2, 1]", encoding="utf-8")

    assert bot.load_used_set(json_path) == {1, 2, 11}
    assert json.loads(json_path.read_text(encoding="utf-8")) == [1, 2, 11]


def test_used_history_missing_without_legacy_pickle_returns_empty_set(tmp_path: Path) -> None:
    json_path = tmp_path / "missing.json"

    assert bot.load_used_set(json_path) == set()


def test_choose_unused_line_returns_non_empty_line_and_marks_empty_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines_file = tmp_path / "mrsMThatcher.txt"
    lines_file.write_text("\nA usable quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(["", "A usable quote."]))
    monkeypatch.setattr(bot.random, "shuffle", lambda values: values.sort())

    used: set[str] = set()
    line_no, text = bot.choose_unused_line(used)

    assert (line_no, text) == (1, "A usable quote.")
    assert used == set()


def test_choose_unused_line_resets_when_all_lines_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines_file = tmp_path / "mrsMThatcher.txt"
    lines_file.write_text("Only quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    only_hash = bot.quote_text_hash("Only quote.")
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(["Only quote."]))

    used = {only_hash}
    line_no, text = bot.choose_unused_line(used)

    assert (line_no, text) == (0, "Only quote.")
    assert used == set()


def test_choose_unused_image_resets_when_all_images_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))

    used = {"t01.jpg"}
    image_no, selected = bot.choose_unused_image(used)

    assert image_no == 0
    assert selected == str(image_path)
    assert used == set()


def test_quote_metadata_uses_current_quote_hash_not_stale_line_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Quote text\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    quote_hash = bot.quote_text_hash("Quote text")
    analysis = {"primary_topics": ["government"]}
    data = {"line_index": {"1": "stale"}, "items": {quote_hash: {"text": "Quote text", "analysis": analysis}}}

    assert bot.quote_metadata_for_line(data, 0) == (quote_hash, analysis)


def test_quote_override_deep_merges_only_requested_field() -> None:
    quote_hash = "hash1"
    raw = {
        "items": {
            quote_hash: {
                "text": "Quote text",
                "line_numbers": [175],
                "analysis": {
                    "seasonality": {
                        "hard_exclude_outside_windows": True,
                        "relevance": "strong",
                    },
                    "scores": {"general_post_suitability": 80},
                },
            }
        }
    }
    overrides = {
        "quote_overrides": {
            quote_hash: {
                "expected_line_numbers": [175],
                "expected_text": "Quote text",
                "analysis_patch": {"seasonality": {"hard_exclude_outside_windows": False}},
            }
        }
    }

    merged = bot.apply_quote_analysis_overrides(raw, overrides)

    assert raw["items"][quote_hash]["analysis"]["seasonality"]["hard_exclude_outside_windows"] is True
    assert merged["items"][quote_hash]["analysis"]["seasonality"]["hard_exclude_outside_windows"] is False
    assert merged["items"][quote_hash]["analysis"]["seasonality"]["relevance"] == "strong"
    assert merged["items"][quote_hash]["analysis"]["scores"]["general_post_suitability"] == 80


def test_stale_quote_override_is_warned_and_skipped(caplog: pytest.LogCaptureFixture) -> None:
    quote_hash = "hash1"
    raw = {
        "items": {
            quote_hash: {
                "text": "Current text",
                "line_numbers": [175],
                "analysis": {"seasonality": {"hard_exclude_outside_windows": True}},
            }
        }
    }
    overrides = {
        "quote_overrides": {
            quote_hash: {
                "expected_line_numbers": [175],
                "expected_text": "Old text",
                "analysis_patch": {"seasonality": {"hard_exclude_outside_windows": False}},
            }
        }
    }

    merged = bot.apply_quote_analysis_overrides(raw, overrides)

    assert merged["items"][quote_hash]["analysis"]["seasonality"]["hard_exclude_outside_windows"] is True
    assert "expected_text does not match" in caplog.text


def test_mm_dd_window_matching_supports_normal_and_cross_year_windows() -> None:
    assert bot.mm_dd_in_window("12-20", "12-10", "12-28")
    assert not bot.mm_dd_in_window("01-05", "12-10", "12-28")
    assert bot.mm_dd_in_window("01-05", "12-01", "02-28")
    assert bot.mm_dd_in_window("12-20", "12-01", "02-28")
    assert not bot.mm_dd_in_window("03-01", "12-01", "02-28")


def test_hard_seasonal_exclusion_outside_window() -> None:
    analysis = {
        "seasonality": {
            "hard_exclude_outside_windows": True,
            "relevance": "strong",
            "preferred_windows": [{"start_mm_dd": "12-10", "end_mm_dd": "12-28"}],
        }
    }

    status = bot.quote_season_status(analysis, today_mm_dd="07-05")

    assert status["hard_excluded"] is True
    assert bot.quote_candidate_weight(analysis, today_mm_dd="07-05")[0] == 0


def test_quote_cycle_seasonal_exhaustion_resets_without_selecting_hard_excluded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Normal quote.\nChristmas quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    normal_hash = bot.quote_text_hash("Normal quote.")
    christmas_analysis = {
        "seasonality": {
            "hard_exclude_outside_windows": True,
            "preferred_windows": [{"start_mm_dd": "12-10", "end_mm_dd": "12-28"}],
            "relevance": "strong",
        }
    }
    monkeypatch.setattr(
        bot,
        "load_quote_analysis",
        lambda: quote_analysis_for_lines(["Normal quote.", "Christmas quote."], {1: christmas_analysis}),
    )

    used = {normal_hash}
    chosen = bot.choose_unused_line_candidate(used)

    assert chosen["line_no"] == 0
    assert used == set()


def test_quote_cycle_resets_when_only_unused_quote_is_unanalysed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = ["Analysed quote.", "New unanalysed quote."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    analysed = quote_analysis_for_lines(["Analysed quote."])
    analysed_hash = bot.quote_text_hash("Analysed quote.")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: analysed)

    chosen = bot.choose_unused_line_candidate({analysed_hash})

    assert chosen["text"] == "Analysed quote."


def test_quote_cycle_resets_when_remaining_unused_quotes_are_unanalysed_and_hard_seasonal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = ["Analysed quote.", "Edited unanalysed quote.", "Christmas quote."]
    christmas = {
        "seasonality": {
            "hard_exclude_outside_windows": True,
            "preferred_windows": [{"start_mm_dd": "12-10", "end_mm_dd": "12-28"}],
            "relevance": "strong",
        }
    }
    analysed = quote_analysis_for_lines(["Analysed quote.", "Christmas quote."], {1: christmas})
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: analysed)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))

    chosen = bot.choose_unused_line_candidate({bot.quote_text_hash("Analysed quote.")})

    assert chosen["text"] == "Analysed quote."


def test_strong_in_window_quote_weight_exceeds_ordinary_weight() -> None:
    ordinary = {"seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"}}
    strong = {
        "seasonality": {
            "hard_exclude_outside_windows": False,
            "relevance": "strong",
            "preferred_windows": [{"start_mm_dd": "12-10", "end_mm_dd": "12-28"}],
        }
    }

    assert bot.quote_candidate_weight(strong, today_mm_dd="12-20")[0] > bot.quote_candidate_weight(ordinary, today_mm_dd="12-20")[0]


def test_image_used_history_integer_entries_migrate_to_basenames(tmp_path: Path) -> None:
    paths = [tmp_path / "t01.jpg", tmp_path / "t02.jpg"]
    for path in paths:
        path.write_bytes(path.name.encode("utf-8"))
    images = [str(path) for path in paths]

    migrated, changed = bot.normalise_image_used_basenames({0, 1}, images, image_analysis_for_paths(paths))

    assert migrated == {"t01.jpg", "t02.jpg"}
    assert changed is True


def test_image_used_history_mixed_entries_migrate_and_preserve_missing_basenames(tmp_path: Path) -> None:
    paths = [tmp_path / "t01.jpg", tmp_path / "t02.jpg"]
    for path in paths:
        path.write_bytes(path.name.encode("utf-8"))
    images = [str(path) for path in paths]

    migrated, changed = bot.normalise_image_used_basenames({0, "t02.jpg", "missing.jpg"}, images, image_analysis_for_paths(paths))

    assert migrated == {"t01.jpg", "t02.jpg", "missing.jpg"}
    assert changed is True


def test_image_cycle_remaining_set_prevents_repeat_until_unused_exhausted(tmp_path: Path) -> None:
    images = [str(tmp_path / "t01.jpg"), str(tmp_path / "t02.jpg")]

    available, reset = bot.available_image_basenames(images, {"t01.jpg"}, {})

    assert available == ["t02.jpg"]
    assert reset is False


def test_image_cycle_resets_only_after_all_images_used_and_avoids_boundary_duplicate(tmp_path: Path) -> None:
    images = [str(tmp_path / "t01.jpg"), str(tmp_path / "t02.jpg")]
    used = {"t01.jpg", "t02.jpg"}

    available, reset = bot.available_image_basenames(images, used, {"last_regular_image_filename": "t02.jpg"})

    assert reset is True
    assert used == set()
    assert available == ["t01.jpg"]


def test_currently_eligible_image_cycle_resets_without_marking_seasonal_image_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_paths = []
    for name in ["t01.jpg", "t02.jpg", "t23.jpg"]:
        path = image_dir / name
        path.write_bytes(name.encode("utf-8"))
        image_paths.append(path)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(
        bot,
        "load_image_analysis",
        lambda: image_analysis_for_paths(
            image_paths,
            {
                "t01.jpg": {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}},
                "t02.jpg": {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}},
                "t23.jpg": {
                        "pairing": {},
                        "themes": [],
                        "tone": [],
                        "visual_energy": "low",
                        "quality": {},
                        "seasonality": {
                            "avoid_outside_season_or_occasion": True,
                            "occasions": ["christmas"],
                            "visible_season": "winter",
                        },
                },
            },
        ),
    )

    used = {"t01.jpg", "t02.jpg"}
    chosen = bot.choose_matched_unused_image(used, {"analysis": {"primary_topics": [], "secondary_topics": [], "tone": [], "visual_energy": "low"}}, {})

    assert chosen["basename"] in {"t01.jpg", "t02.jpg"}
    assert "t23.jpg" not in used
    assert used == set()


def test_seasonal_image_re_enters_when_current_date_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_paths = []
    for name in ["t01.jpg", "t23.jpg"]:
        path = image_dir / name
        path.write_bytes(name.encode("utf-8"))
        image_paths.append(path)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 12, 20))
    monkeypatch.setattr(
        bot,
        "load_image_analysis",
        lambda: image_analysis_for_paths(
            image_paths,
            {
                "t01.jpg": {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}},
                "t23.jpg": {
                        "pairing": {"best_for_topics": ["christmas"]},
                        "themes": ["christmas"],
                        "tone": [],
                        "visual_energy": "low",
                        "quality": {},
                        "seasonality": {
                            "avoid_outside_season_or_occasion": True,
                            "occasions": ["christmas"],
                            "visible_season": "winter",
                        },
                },
            },
        ),
    )

    chosen = bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["christmas"], "secondary_topics": [], "tone": [], "visual_energy": "low"}}, {})

    assert chosen["basename"] == "t23.jpg"


def test_generated_image_pool_disabled_keeps_original_image_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    generated = generated_dir / ("tg_" + "a" * 64 + ".png")
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", False)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))

    assert bot.current_image_paths() == [str(original)]


def test_generated_image_pool_merges_separate_analysis_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    quote_hash = "b" * 64
    generated = generated_dir / f"tg_{quote_hash}.png"
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    write_image_analysis(original_analysis_path, image_analysis_for_paths([original]))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths([generated]))

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))

    paths = bot.current_image_paths()
    analysis = bot.load_image_analysis()

    assert paths == [str(original), str(generated)]
    assert set(analysis["path_index"]) == {"t01.jpg", f"tg_{quote_hash}.png"}


def test_generated_image_pool_rejects_paths_outside_generated_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    outside_dir = tmp_path / "outside"
    image_dir.mkdir()
    generated_dir.mkdir()
    outside_dir.mkdir()
    original = image_dir / "t01.jpg"
    outside = outside_dir / ("tg_" + "e" * 64 + ".png")
    original.write_bytes(b"original")
    outside.write_bytes(b"outside")

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "../outside/*.png")

    assert bot.current_image_paths() == [str(original)]


def test_generated_image_source_classification() -> None:
    quote_hash = "a" * 64

    assert bot.GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN == 2
    assert bot.image_selection_observability("t44.jpg") == {
        "image_source": "original",
        "origin_quote_hash": None,
        "origin_quote_match": False,
        "origin_quote_boost": 0.0,
    }
    assert bot.image_selection_observability(f"tg_{quote_hash}.png", quote_hash, 4) == {
        "image_source": "generated",
        "origin_quote_hash": quote_hash,
        "origin_quote_match": True,
        "origin_quote_boost": 4.0,
    }
    assert bot.image_selection_observability(f"tg_{quote_hash}.png", "b" * 64, 4) == {
        "image_source": "generated",
        "origin_quote_hash": quote_hash,
        "origin_quote_match": False,
        "origin_quote_boost": 0.0,
    }
    assert bot.generated_image_origin_quote_hash("tg_not-a-real-hash.png") is None
    assert bot.generated_image_origin_quote_hash("tg_" + ("a" * 63) + ".png") is None


def test_generated_image_spacing_helpers_and_state_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    generated = "tg_" + ("a" * 64) + ".png"
    state = {"original_regular_posts_since_generated_image": 0}
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)

    assert bot.generated_images_allowed_by_spacing(state) is False
    bot.update_regular_generated_image_spacing_state(state, "t01.jpg")
    assert state["original_regular_posts_since_generated_image"] == 1
    assert bot.generated_images_allowed_by_spacing(state) is False
    bot.update_regular_generated_image_spacing_state(state, "t02.jpg")
    assert state["original_regular_posts_since_generated_image"] == 2
    assert bot.generated_images_allowed_by_spacing(state) is True
    bot.update_regular_generated_image_spacing_state(state, "t03.jpg")
    assert state["original_regular_posts_since_generated_image"] == 2
    bot.update_regular_generated_image_spacing_state(state, generated)
    assert state["original_regular_posts_since_generated_image"] == 0


@pytest.mark.parametrize("value", [0, 2])
def test_generated_image_spacing_required_accepts_integers(monkeypatch: pytest.MonkeyPatch, value: int) -> None:
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", value)

    assert bot.generated_image_spacing_required() == value


def test_generated_image_spacing_zero_disables_restriction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 0)

    assert bot.generated_images_allowed_by_spacing({"original_regular_posts_since_generated_image": 0}) is True


@pytest.mark.parametrize("bad_value", [True, False, -1, 2.0, 2.5, "2", "x"])
def test_generated_image_spacing_helper_rejects_non_integer_values(
    monkeypatch: pytest.MonkeyPatch,
    bad_value: object,
) -> None:
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", bad_value)

    with pytest.raises(ValueError):
        bot.generated_image_spacing_required()


@pytest.mark.parametrize(
    ("last_image", "expected_count"),
    [
        ("tg_" + ("a" * 64) + ".png", 0),
        ("t01.jpg", 2),
        (None, 2),
    ],
)
def test_legacy_state_initialises_generated_spacing_from_last_regular_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    last_image: str | None,
    expected_count: int,
) -> None:
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
    state = bot.default_state()
    state.pop("original_regular_posts_since_generated_image", None)
    state["last_regular_image_filename"] = last_image

    normalised = bot.normalise_state_candidate(state, path=tmp_path / "state.json")

    assert normalised is not None
    assert normalised["original_regular_posts_since_generated_image"] == expected_count


def test_state_rejects_boolean_generated_spacing_counter(tmp_path: Path) -> None:
    state = bot.default_state()
    state["original_regular_posts_since_generated_image"] = True

    assert bot.normalise_state_candidate(state, path=tmp_path / "state.json") is None


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


@pytest.mark.parametrize("value", [0, 2])
def test_runtime_config_validation_accepts_integer_generated_spacing(value: int) -> None:
    assert not bot.validate_runtime_config_values({"GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": value})


@pytest.mark.parametrize("bad_value", [True, False, 2.0, 2.5, "2", "x"])
def test_runtime_config_validation_rejects_non_integer_generated_spacing(bad_value: object) -> None:
    errors = bot.validate_runtime_config_values({"GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": bad_value})
    assert "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN must be an integer" in errors


def test_runtime_config_validation_rejects_negative_generated_spacing() -> None:
    errors = bot.validate_runtime_config_values({"GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": -1})

    assert "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN must be non-negative" in errors


def test_original_image_selection_returns_observability_and_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    original = image_dir / "t44.jpg"
    original.write_bytes(b"original")
    analysis_path = tmp_path / "image_analysis.json"
    analysis = image_analysis_for_paths([original])
    write_image_analysis(analysis_path, analysis)

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", False)
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", analysis_path)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    caplog.set_level(logging.INFO, logger=bot.log.name)

    chosen = bot.choose_matched_unused_image(
        set(),
        {"quote_hash": "f" * 64, "analysis": {"primary_topics": [], "secondary_topics": [], "tone": [], "visual_energy": "low"}},
        {},
    )

    assert chosen["basename"] == "t44.jpg"
    assert chosen["image_source"] == "original"
    assert chosen["origin_quote_hash"] is None
    assert chosen["origin_quote_match"] is False
    assert chosen["origin_quote_boost"] == 0.0
    assert "REGULAR_IMAGE_SELECTED source=original basename=t44.jpg" in caplog.text
    assert "origin_quote_match=false origin_quote_boost=0.0" in caplog.text


def test_generated_image_origin_quote_boost_can_select_matching_generated_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    quote_hash = "c" * 64
    generated = generated_dir / f"tg_{quote_hash}.png"
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    neutral = {
        "pairing": {},
        "themes": [],
        "tone": [],
        "visual_energy": "low",
        "quality": {},
        "seasonality": {"avoid_outside_season_or_occasion": False},
    }
    write_image_analysis(original_analysis_path, image_analysis_for_paths([original], {"t01.jpg": neutral}))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths([generated], {generated.name: neutral}))

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST", 10)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))

    chosen = bot.choose_matched_unused_image(
        set(),
        {
            "quote_hash": quote_hash,
            "analysis": {"primary_topics": [], "secondary_topics": [], "tone": [], "visual_energy": "low"},
        },
        {},
    )

    assert chosen["basename"] == generated.name
    assert chosen["components"]["generated_origin_quote"] == 10.0
    assert chosen["image_source"] == "generated"
    assert chosen["origin_quote_hash"] == quote_hash
    assert chosen["origin_quote_match"] is True
    assert chosen["origin_quote_boost"] == 10.0


def test_generated_image_cross_quote_selection_gets_no_origin_boost_and_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated_dir = tmp_path / "generated"
    image_dir = tmp_path / "images"
    generated_dir.mkdir()
    image_dir.mkdir()
    origin_quote_hash = "a" * 64
    selected_quote_hash = "b" * 64
    generated = generated_dir / f"tg_{origin_quote_hash}.png"
    generated.write_bytes(b"generated")
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    write_image_analysis(original_analysis_path, image_analysis_for_paths([]))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths([generated]))

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST", 10)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    caplog.set_level(logging.INFO, logger=bot.log.name)

    chosen = bot.choose_matched_unused_image(
        set(),
        {
            "quote_hash": selected_quote_hash,
            "analysis": {"primary_topics": [], "secondary_topics": [], "tone": [], "visual_energy": "low"},
        },
        {},
    )

    assert chosen["basename"] == generated.name
    assert chosen["image_source"] == "generated"
    assert chosen["origin_quote_hash"] == origin_quote_hash
    assert chosen["origin_quote_match"] is False
    assert chosen["origin_quote_boost"] == 0.0
    assert "generated_origin_quote" not in chosen["components"]
    assert f"REGULAR_IMAGE_SELECTED source=generated basename={generated.name}" in caplog.text
    assert f"origin_quote_hash={origin_quote_hash} origin_quote_match=false origin_quote_boost=0.0" in caplog.text


def test_generated_image_blocked_by_spacing_selects_original_without_marking_generated_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    generated_hash = "a" * 64
    generated = generated_dir / f"tg_{generated_hash}.png"
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Tax quote.\n", encoding="utf-8")
    quote_analysis = {"primary_topics": ["tax"], "secondary_topics": [], "tone": [], "visual_energy": "low"}
    original_analysis = {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}}
    generated_analysis = {"pairing": {"best_for_topics": ["tax"]}, "themes": ["tax"], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}}
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    write_image_analysis(original_analysis_path, image_analysis_for_paths([original], {original.name: original_analysis}))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths([generated], {generated.name: generated_analysis}))

    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(["Tax quote."], {0: quote_analysis}))
    caplog.set_level(logging.INFO, logger=bot.log.name)
    images_used: set[str] = set()
    state = {"original_regular_posts_since_generated_image": 0}

    chosen = bot.choose_regular_quote_image_pair(
        set(),
        images_used,
        state,
    )[1]

    assert chosen["basename"] == "t01.jpg"
    assert chosen["image_source"] == "original"
    assert generated.name not in images_used
    assert "GENERATED_IMAGE_SPACING_STATUS pool_enabled=true allowed=false original_posts_since_generated=0 required=2" in caplog.text
    assert "GENERATED_IMAGE_POOL_BLOCKED_BY_SPACING original_posts_since_generated=0 required=2" in caplog.text


def test_generated_image_spacing_expiry_allows_generated_to_compete_with_origin_boost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    quote = "Origin boost quote."
    quote_hash = bot.quote_text_hash(quote)
    generated = generated_dir / f"tg_{quote_hash}.png"
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text(quote + "\n", encoding="utf-8")
    neutral = {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}}
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    write_image_analysis(original_analysis_path, image_analysis_for_paths([original], {original.name: neutral}))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths([generated], {generated.name: neutral}))

    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST", 4)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines([quote]))

    chosen = bot.choose_regular_quote_image_pair(
        set(),
        set(),
        {"original_regular_posts_since_generated_image": 2},
    )[1]

    assert chosen["basename"] == generated.name
    assert chosen["image_source"] == "generated"
    assert chosen["origin_quote_match"] is True
    assert chosen["origin_quote_boost"] == 4.0


def test_generated_origin_boost_preserves_expected_final_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated_dir = tmp_path / "generated"
    image_dir = tmp_path / "images"
    generated_dir.mkdir()
    image_dir.mkdir()
    quote_hash = "c" * 64
    generated = generated_dir / f"tg_{quote_hash}.png"
    generated.write_bytes(b"generated")
    analysis_obj = {
        "pairing": {"best_for_topics": ["trade"]},
        "themes": [],
        "tone": [],
        "visual_energy": "low",
        "quality": {},
        "seasonality": {"avoid_outside_season_or_occasion": False},
    }
    quote_analysis = {"primary_topics": ["trade"], "secondary_topics": [], "tone": [], "visual_energy": "low"}
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    write_image_analysis(original_analysis_path, image_analysis_for_paths([]))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths([generated], {generated.name: analysis_obj}))

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST", 6)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))

    base_score = bot.score_image_for_quote(quote_analysis, analysis_obj, bot.build_image_topic_idf(bot.load_image_analysis()))[0]
    origin_choice = bot.choose_matched_unused_image(set(), {"quote_hash": quote_hash, "analysis": quote_analysis}, {})
    cross_choice = bot.choose_matched_unused_image(set(), {"quote_hash": "d" * 64, "analysis": quote_analysis}, {})

    assert origin_choice["score"] == pytest.approx(base_score + 6)
    assert origin_choice["origin_quote_boost"] == 6.0
    assert cross_choice["score"] == pytest.approx(base_score)
    assert cross_choice["origin_quote_boost"] == 0.0


def test_generated_image_pool_enabled_does_not_migrate_legacy_image_indices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    generated = generated_dir / ("tg_" + "d" * 64 + ".png")
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")
    analysis = bot.merge_image_analysis(
        image_analysis_for_paths([original]),
        image_analysis_for_paths([generated]),
    )
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)

    normalised, changed = bot.normalise_image_used_basenames({0}, [str(original), str(generated)], analysis)

    assert normalised == {0}
    assert not changed


def test_highest_scoring_unused_image_is_selected_even_if_global_best_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_paths = []
    for name in ["t01.jpg", "t02.jpg"]:
        path = image_dir / name
        path.write_bytes(name.encode("utf-8"))
        image_paths.append(path)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(
        bot,
        "load_image_analysis",
        lambda: image_analysis_for_paths(
            image_paths,
            {
                "t01.jpg": {"pairing": {"best_for_topics": ["rare_topic"]}, "themes": ["rare_topic"], "visual_energy": "low", "quality": {}},
                "t02.jpg": {"pairing": {"best_for_topics": ["government"]}, "themes": [], "visual_energy": "low", "quality": {}},
            },
        ),
    )

    chosen = bot.choose_matched_unused_image(
        {"t01.jpg"},
        {"analysis": {"primary_topics": ["rare_topic", "government"], "secondary_topics": [], "tone": [], "visual_energy": "low"}},
        {},
    )

    assert chosen["basename"] == "t02.jpg"


def test_idf_makes_rare_topic_match_more_valuable_than_ubiquitous_topic() -> None:
    image_analysis = {
        "items": {
            "a": {"analysis": {"pairing": {"best_for_topics": ["leadership", "rare_topic"]}, "themes": []}},
            "b": {"analysis": {"pairing": {"best_for_topics": ["leadership"]}, "themes": []}},
            "c": {"analysis": {"pairing": {"best_for_topics": ["leadership"]}, "themes": []}},
        }
    }
    idf = bot.build_image_topic_idf(image_analysis)
    quote = {"primary_topics": ["rare_topic"], "secondary_topics": [], "tone": [], "visual_energy": "low"}
    rare_image = {"pairing": {"best_for_topics": ["rare_topic"]}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}}
    common_image = {"pairing": {"best_for_topics": ["leadership"]}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}}

    rare_score = bot.score_image_for_quote(quote, rare_image, idf)[0]
    common_score = bot.score_image_for_quote({"primary_topics": ["leadership"], "secondary_topics": [], "tone": [], "visual_energy": "low"}, common_image, idf)[0]

    assert rare_score > common_score


def test_visual_energy_exact_match_beats_opposite_mismatch() -> None:
    quote = {"primary_topics": [], "secondary_topics": [], "tone": [], "visual_energy": "high"}
    exact = {"pairing": {}, "themes": [], "tone": [], "visual_energy": "high", "quality": {}}
    opposite = {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}}

    assert bot.score_image_for_quote(quote, exact)[0] > bot.score_image_for_quote(quote, opposite)[0]


def test_strong_visual_contradiction_is_ineligible() -> None:
    quote = {
        "primary_topics": [],
        "secondary_topics": [],
        "tone": [],
        "visual_energy": "low",
        "archive_image_preferences": {"strong_visual_mismatches": ["crowd scenes"]},
    }
    image = {"description": "A large crowd scene outside a building.", "pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}}

    score, components, eligible = bot.score_image_for_quote(quote, image)

    assert eligible is False
    assert score <= bot.IMAGE_STRONG_MISMATCH_PENALTY
    assert "strong_mismatch" in components


def test_hard_mismatch_requires_both_tokens_for_two_token_phrase() -> None:
    assert not bot.hard_mismatch_phrase_matches_text("armed conflict", "An armed police officer stands nearby.")
    assert bot.hard_mismatch_phrase_matches_text("armed conflict", "An armed conflict scene is shown.")


def test_hard_mismatch_longer_phrase_uses_ceil_coverage() -> None:
    assert not bot.hard_mismatch_phrase_matches_text("large military parade crowd", "Large military display")
    assert bot.hard_mismatch_phrase_matches_text("large military parade crowd", "Large military parade crowd")


def test_post_random_quote_retries_alternate_quote_when_first_has_no_image_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Bad visual quote.\nGood visual quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "LINES_USED_FILE", tmp_path / "lines_used.json")
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", tmp_path / "images_used.json")
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot.random, "uniform", lambda low, high: low)
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "950001"}})
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "maybe_schedule_meme_after_quote_post", lambda state, quote_post_epoch=None, **kwargs: None)
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        bot,
        "load_quote_analysis",
        lambda: quote_analysis_for_lines(
            ["Bad visual quote.", "Good visual quote."],
            {
                0: {
                        "archive_image_preferences": {"strong_visual_mismatches": ["formal portrait"]},
                        "seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"},
                },
                1: {
                        "archive_image_preferences": {},
                        "seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"},
                },
            },
        ),
    )
    monkeypatch.setattr(
        bot,
        "load_image_analysis",
        lambda: image_analysis_for_paths(
            [image_path],
            {
                "t01.jpg": {
                        "description": "Formal portrait in a studio",
                        "pairing": {},
                        "themes": [],
                        "tone": [],
                        "visual_energy": "low",
                        "quality": {},
                        "seasonality": {"avoid_outside_season_or_occasion": False},
                }
            },
        ),
    )

    lines_used: set[int] = set()
    images_used: set[str] = set()
    state: dict = {}
    bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == {bot.quote_text_hash("Good visual quote.")}
    assert images_used == {"t01.jpg"}
    assert state["last_regular_image_filename"] == "t01.jpg"


def configure_generated_cycle_recovery_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    original_analyses: dict[str, dict],
    generated_analyses: dict[str, dict],
    quote_analyses: dict[int, dict],
    quotes: list[str] | None = None,
) -> tuple[set[str], set[str], dict, dict[str, Path], dict[str, Path]]:
    quotes = quotes or ["Quote A.", "Quote B."]
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original_paths: dict[str, Path] = {}
    generated_paths: dict[str, Path] = {}
    for basename in original_analyses:
        path = image_dir / basename
        path.write_bytes(f"original-{basename}".encode("utf-8"))
        original_paths[basename] = path
    for basename in generated_analyses:
        path = generated_dir / basename
        path.write_bytes(f"generated-{basename}".encode("utf-8"))
        generated_paths[basename] = path

    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(quotes) + "\n", encoding="utf-8")
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    write_image_analysis(original_analysis_path, image_analysis_for_paths(list(original_paths.values()), original_analyses))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths(list(generated_paths.values()), generated_analyses))

    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))
    monkeypatch.setattr(bot, "LINES_USED_FILE", tmp_path / "lines_used.json")
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", tmp_path / "images_used.json")
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", tmp_path / "regular_post_receipt.json")
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot.random, "uniform", lambda low, high: low)
    monkeypatch.setattr(bot.random, "randint", lambda low, high: low)
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "950001"}})
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(quotes, quote_analyses))
    return set(), set(), {}, original_paths, generated_paths


def capture_create_post_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake_create_post(**kwargs: object) -> dict:
        calls.append(dict(kwargs))
        return {"data": {"id": "950001"}}

    monkeypatch.setattr(bot, "create_post", fake_create_post)
    return calls


def portrait_analysis() -> dict:
    return {
        "description": "Formal portrait in a studio",
        "pairing": {},
        "themes": [],
        "tone": [],
        "visual_energy": "low",
        "quality": {},
        "seasonality": {"avoid_outside_season_or_occasion": False},
    }


def crowd_scene_analysis() -> dict:
    return {
        "description": "A large crowd scene outside a building",
        "pairing": {},
        "themes": [],
        "tone": [],
        "visual_energy": "low",
        "quality": {},
        "seasonality": {"avoid_outside_season_or_occasion": False},
    }


def quote_rejecting_crowd_scenes() -> dict:
    return {
        "archive_image_preferences": {"strong_visual_mismatches": ["crowd scene"]},
        "seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"},
        "primary_topics": [],
        "secondary_topics": [],
        "tone": [],
        "visual_energy": "low",
    }


def test_post_random_quote_recovers_when_remaining_generated_cycle_image_cannot_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated_hash = "a" * 64
    generated_name = f"tg_{generated_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": portrait_analysis()},
        generated_analyses={generated_name: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes(), 1: quote_rejecting_crowd_scenes()},
    )
    images_used.add("t01.jpg")
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert images_used == {"t01.jpg"}
    assert state["last_regular_image_filename"] == "t01.jpg"
    assert lines_used == {bot.quote_text_hash("Quote A.")}
    assert "resetting image cycle and retrying once" in caplog.text
    assert "Regular quote/image pairing succeeded after image-cycle recovery" in caplog.text
    assert "last image permitted" not in caplog.text


def test_image_cycle_recovery_does_not_reenable_spacing_blocked_generated_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated_hash = "9" * 64
    generated_name = f"tg_{generated_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": crowd_scene_analysis()},
        generated_analyses={generated_name: portrait_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.add("t01.jpg")
    state["original_regular_posts_since_generated_image"] = 0
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
    create_calls = capture_create_post_calls(monkeypatch)
    caplog.set_level(logging.INFO, logger=bot.log.name)

    with pytest.raises(RuntimeError, match="No eligible regular quote/image pair found"):
        bot.post_random_quote(lines_used, images_used, state)

    assert state.get("last_regular_image_filename") is None
    assert images_used == {"t01.jpg"}
    assert generated_name not in images_used
    assert create_calls == []
    assert state["original_regular_posts_since_generated_image"] == 0
    assert "resetting image cycle and retrying once" in caplog.text
    assert "GENERATED_IMAGE_POOL_BLOCKED_BY_SPACING" in caplog.text
    assert f"REGULAR_IMAGE_SELECTED source=generated basename={generated_name}" not in caplog.text


def test_last_image_boundary_fallback_recovers_two_image_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated_hash = "f" * 64
    generated_name = f"tg_{generated_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": portrait_analysis()},
        generated_analyses={generated_name: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.add("t01.jpg")
    state["last_regular_image_filename"] = "t01.jpg"
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_regular_image_filename"] == "t01.jpg"
    assert images_used == {"t01.jpg"}
    assert lines_used == {bot.quote_text_hash("Only quote.")}
    assert "retrying once with last image permitted" in caplog.text
    assert "Regular quote/image pairing succeeded after permitting last regular image" in caplog.text


def test_last_image_boundary_avoidance_wins_when_non_last_image_is_viable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated_hash = "1" * 64
    generated_name = f"tg_{generated_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": portrait_analysis(), "t02.jpg": portrait_analysis()},
        generated_analyses={generated_name: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.update({"t01.jpg", "t02.jpg"})
    state["last_regular_image_filename"] = "t01.jpg"
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_regular_image_filename"] == "t02.jpg"
    assert images_used == {"t02.jpg"}
    assert "Temporarily excluded last regular image at forced eligible-cycle boundary: t01.jpg" in caplog.text
    assert "retrying once with last image permitted" not in caplog.text


def test_last_image_boundary_fallback_fails_safely_without_fourth_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated_hash = "2" * 64
    generated_name = f"tg_{generated_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": crowd_scene_analysis()},
        generated_analyses={generated_name: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.add("t01.jpg")
    state["last_regular_image_filename"] = "t01.jpg"
    original_lines = set(lines_used)
    original_images = set(images_used)
    monkeypatch.setattr(bot, "upload_media", lambda path: pytest.fail("upload_media should not be called"))
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called"))
    caplog.set_level(logging.INFO, logger=bot.log.name)

    with pytest.raises(RuntimeError, match="No eligible regular quote/image pair found"):
        bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == original_lines
    assert images_used == original_images
    assert caplog.text.count("Forcing eligible image cycle reset for regular quote/image pairing recovery") == 2
    assert caplog.text.count("retrying once with last image permitted") == 1
    assert "No viable regular quote/image pair found after final last-image recovery fallback" in caplog.text


def test_no_last_image_does_not_trigger_last_image_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated_hash = "3" * 64
    generated_name = f"tg_{generated_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": portrait_analysis()},
        generated_analyses={generated_name: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.add("t01.jpg")
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_regular_image_filename"] == "t01.jpg"
    assert "retrying once with last image permitted" not in caplog.text


def test_last_image_boundary_fallback_can_reuse_generated_previous_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    good_hash = "4" * 64
    bad_hash = "5" * 64
    good_generated = f"tg_{good_hash}.png"
    bad_generated = f"tg_{bad_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={},
        generated_analyses={
            good_generated: portrait_analysis(),
            bad_generated: crowd_scene_analysis(),
        },
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.add(good_generated)
    state["last_regular_image_filename"] = good_generated
    create_calls = capture_create_post_calls(monkeypatch)
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_regular_image_filename"] == good_generated
    assert images_used == {good_generated}
    assert len(create_calls) == 1
    assert create_calls[0]["made_with_ai"] is True
    assert "retrying once with last image permitted" in caplog.text
    assert "REGULAR_IMAGE_SELECTED source=generated" in caplog.text


def test_last_image_fallback_does_not_reenable_spacing_blocked_generated_previous_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    last_hash = "8" * 64
    last_generated = f"tg_{last_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": crowd_scene_analysis()},
        generated_analyses={last_generated: portrait_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.add(last_generated)
    state["last_regular_image_filename"] = last_generated
    state["original_regular_posts_since_generated_image"] = 0
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
    monkeypatch.setattr(bot, "upload_media", lambda path: pytest.fail("upload_media should not be called"))
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called"))
    caplog.set_level(logging.INFO, logger=bot.log.name)

    with pytest.raises(RuntimeError, match="No eligible regular quote/image pair found"):
        bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == set()
    assert images_used == {last_generated}
    assert "retrying once with last image permitted" not in caplog.text
    assert f"REGULAR_IMAGE_SELECTED source=generated basename={last_generated}" not in caplog.text


def test_post_random_quote_image_cycle_recovery_fails_safely_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated_hash = "b" * 64
    generated_name = f"tg_{generated_hash}.png"
    lines_used, images_used, _state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": crowd_scene_analysis()},
        generated_analyses={generated_name: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes(), 1: quote_rejecting_crowd_scenes()},
    )
    images_used.add("t01.jpg")
    original_lines = set(lines_used)
    original_images = set(images_used)
    monkeypatch.setattr(bot, "upload_media", lambda path: pytest.fail("upload_media should not be called"))
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called"))
    caplog.set_level(logging.INFO, logger=bot.log.name)

    with pytest.raises(RuntimeError, match="No eligible regular quote/image pair found"):
        bot.post_random_quote(lines_used, images_used, {})

    assert lines_used == original_lines
    assert images_used == original_images
    assert caplog.text.count("resetting image cycle and retrying once") == 1
    assert "No viable regular quote/image pair found after image-cycle recovery" in caplog.text


def test_forced_image_cycle_recovery_respects_generated_last_image_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    last_hash = "c" * 64
    bad_hash = "d" * 64
    last_generated = f"tg_{last_hash}.png"
    bad_generated = f"tg_{bad_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": portrait_analysis()},
        generated_analyses={
            last_generated: portrait_analysis(),
            bad_generated: crowd_scene_analysis(),
        },
        quote_analyses={0: quote_rejecting_crowd_scenes(), 1: quote_rejecting_crowd_scenes()},
    )
    images_used.update({"t01.jpg", last_generated})
    state["last_regular_image_filename"] = last_generated

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_regular_image_filename"] == "t01.jpg"
    assert last_generated not in images_used
    assert "t01.jpg" in images_used


def test_successful_current_cycle_pair_does_not_force_image_cycle_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated_hash = "e" * 64
    generated_name = f"tg_{generated_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": portrait_analysis()},
        generated_analyses={generated_name: portrait_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes(), 1: quote_rejecting_crowd_scenes()},
    )
    images_used.add("t01.jpg")
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_regular_image_filename"] == generated_name
    assert "resetting image cycle and retrying once" not in caplog.text


def test_global_image_failure_does_not_trigger_image_cycle_recovery(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(bot, "reconcile_main_post_receipts", lambda *args, **kwargs: {"regular": False, "meme": False})
    monkeypatch.setattr(bot, "quote_used_history_has_legacy_indices", lambda used: False)
    monkeypatch.setattr(
        bot,
        "choose_unused_line_candidate",
        lambda *args, **kwargs: {
            "line_no": 0,
            "quote_hash": bot.quote_text_hash("Good quote."),
            "text": "Good quote.",
            "analysis": {},
        },
    )
    monkeypatch.setattr(
        bot,
        "choose_matched_unused_image",
        lambda *args, **kwargs: (_ for _ in ()).throw(bot.GlobalImageUnavailable("no images")),
    )
    caplog.set_level(logging.INFO, logger=bot.log.name)

    with pytest.raises(bot.GlobalImageUnavailable):
        bot.post_random_quote(set(), set(), {})

    assert "resetting image cycle and retrying once" not in caplog.text


def test_original_regular_image_posts_without_made_with_ai(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    state["original_regular_posts_since_generated_image"] = 0
    create_calls = capture_create_post_calls(monkeypatch)
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert len(create_calls) == 1
    assert create_calls[0]["made_with_ai"] is False
    assert state["original_regular_posts_since_generated_image"] == 1
    assert "GENERATED_IMAGE_SPACING_STATE_UPDATED pool_enabled=false allowed=false original_posts_since_generated=1 required=2 image_source=original image=t01.jpg" in caplog.text


def test_second_original_regular_image_spacing_update_allows_generated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    state["original_regular_posts_since_generated_image"] = 1
    capture_create_post_calls(monkeypatch)
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["original_regular_posts_since_generated_image"] == 2
    assert "GENERATED_IMAGE_SPACING_STATE_UPDATED pool_enabled=false allowed=true original_posts_since_generated=2 required=2 image_source=original image=t01.jpg" in caplog.text


def test_generated_origin_match_regular_image_posts_with_made_with_ai(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    quote = "Generated origin quote."
    origin_hash = bot.quote_text_hash(quote)
    generated_name = f"tg_{origin_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={},
        generated_analyses={generated_name: portrait_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=[quote],
    )
    create_calls = capture_create_post_calls(monkeypatch)

    bot.post_random_quote(lines_used, images_used, state)

    assert len(create_calls) == 1
    assert create_calls[0]["made_with_ai"] is True
    assert state["last_regular_image_filename"] == generated_name
    assert state["original_regular_posts_since_generated_image"] == 0


def test_generated_cross_quote_regular_image_posts_with_made_with_ai(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    quote = "Generated cross quote."
    origin_hash = "6" * 64
    assert origin_hash != bot.quote_text_hash(quote)
    generated_name = f"tg_{origin_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={},
        generated_analyses={generated_name: portrait_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=[quote],
    )
    create_calls = capture_create_post_calls(monkeypatch)
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert len(create_calls) == 1
    assert create_calls[0]["made_with_ai"] is True
    assert state["last_regular_image_filename"] == generated_name
    assert state["original_regular_posts_since_generated_image"] == 0
    assert f"GENERATED_IMAGE_SPACING_STATE_UPDATED pool_enabled=true allowed=false original_posts_since_generated=0 required=2 image_source=generated image={generated_name}" in caplog.text
    assert f"REGULAR_IMAGE_SELECTED source=generated basename={generated_name}" in caplog.text
    assert f"origin_quote_hash={origin_hash} origin_quote_match=false origin_quote_boost=0.0" in caplog.text


def test_generated_image_selected_after_cycle_recovery_posts_with_made_with_ai(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin_hash = "7" * 64
    generated_name = f"tg_{origin_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": crowd_scene_analysis()},
        generated_analyses={generated_name: portrait_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.add(generated_name)
    create_calls = capture_create_post_calls(monkeypatch)

    bot.post_random_quote(lines_used, images_used, state)

    assert len(create_calls) == 1
    assert create_calls[0]["made_with_ai"] is True
    assert state["last_regular_image_filename"] == generated_name
    assert state["original_regular_posts_since_generated_image"] == 0


def test_failed_regular_post_attempt_does_not_change_generated_spacing_counter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    quote = "Generated failure quote."
    origin_hash = bot.quote_text_hash(quote)
    generated_name = f"tg_{origin_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={},
        generated_analyses={generated_name: portrait_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=[quote],
    )
    state["original_regular_posts_since_generated_image"] = 2
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "banana"}})
    caplog.set_level(logging.INFO, logger=bot.log.name)

    with pytest.raises(RuntimeError, match="did not return a valid post id"):
        bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == set()
    assert images_used == set()
    assert state["original_regular_posts_since_generated_image"] == 2
    assert "GENERATED_IMAGE_SPACING_STATE_UPDATED" not in caplog.text


def test_post_random_quote_restores_histories_when_all_pair_attempts_fail_after_cycle_resets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    t01 = image_dir / "t01.jpg"
    t23 = image_dir / "t23.jpg"
    t01.write_bytes(b"fake-1")
    t23.write_bytes(b"fake-23")
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Christmas quote.\nBad quote A.\nBad quote B.\n", encoding="utf-8")
    lines_used_file = tmp_path / "lines_used.json"
    images_used_file = tmp_path / "images_used.json"
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "LINES_USED_FILE", lines_used_file)
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", images_used_file)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot.random, "uniform", lambda low, high: low)
    monkeypatch.setattr(bot, "upload_media", lambda path: pytest.fail("upload_media should not be called"))
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called"))
    monkeypatch.setattr(
        bot,
        "load_quote_analysis",
        lambda: quote_analysis_for_lines(
            ["Christmas quote.", "Bad quote A.", "Bad quote B."],
            {
                0: {
                        "archive_image_preferences": {},
                        "seasonality": {
                            "hard_exclude_outside_windows": True,
                            "preferred_windows": [{"start": "12-10", "end": "12-28"}],
                            "relevance": "christmas",
                        },
                },
                1: {
                        "archive_image_preferences": {"strong_visual_mismatches": ["formal portrait"]},
                        "seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"},
                },
                2: {
                        "archive_image_preferences": {"strong_visual_mismatches": ["formal portrait"]},
                        "seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"},
                },
            },
        ),
    )
    monkeypatch.setattr(
        bot,
        "load_image_analysis",
        lambda: image_analysis_for_paths(
            [t01, t23],
            {
                "t01.jpg": {
                        "description": "Formal portrait in a studio",
                        "pairing": {},
                        "themes": [],
                        "tone": [],
                        "visual_energy": "low",
                        "quality": {},
                        "seasonality": {"avoid_outside_season_or_occasion": False},
                },
                "t23.jpg": {
                        "description": "Christmas scene",
                        "pairing": {},
                        "themes": [],
                        "tone": [],
                        "visual_energy": "low",
                        "quality": {},
                        "seasonality": {"avoid_outside_season_or_occasion": True, "occasions": ["christmas"]},
                },
            },
        ),
    )

    lines_used: set[str] = {bot.quote_text_hash("Bad quote A."), bot.quote_text_hash("Bad quote B.")}
    images_used: set[str] = {"t01.jpg"}
    original_lines = set(lines_used)
    original_images = set(images_used)

    with pytest.raises(RuntimeError, match="used histories unchanged"):
        bot.post_random_quote(lines_used, images_used, {})

    assert lines_used == original_lines
    assert images_used == original_images
    assert not lines_used_file.exists()
    assert not images_used_file.exists()


def test_post_random_quote_requires_created_post_id_before_marking_histories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    lines_used_file = tmp_path / "lines_used.json"
    images_used_file = tmp_path / "images_used.json"
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "LINES_USED_FILE", lines_used_file)
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", images_used_file)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {}})
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: pytest.fail("save_state should not be called"))
    monkeypatch.setattr(bot, "maybe_schedule_meme_after_quote_post", lambda *args, **kwargs: pytest.fail("meme scheduling should not be called"))
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: pytest.fail("cache_tweet should not be called"))
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: pytest.fail("record_recent_own_post should not be called"))
    monkeypatch.setattr(bot, "log_event", lambda event, **kwargs: events.append((event, kwargs)))
    monkeypatch.setattr(
        bot,
        "load_quote_analysis",
        lambda: quote_analysis_for_lines(
            ["Good quote."],
            {
                0: {
                        "archive_image_preferences": {},
                        "seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"},
                }
            },
        ),
    )
    monkeypatch.setattr(
        bot,
        "load_image_analysis",
        lambda: image_analysis_for_paths(
            [image_path],
            {
                "t01.jpg": {
                        "description": "A usable image",
                        "pairing": {},
                        "themes": [],
                        "tone": [],
                        "visual_energy": "low",
                        "quality": {},
                        "seasonality": {"avoid_outside_season_or_occasion": False},
                }
            },
        ),
    )

    lines_used: set[int] = set()
    images_used: set[str] = set()
    state = {"last_regular_image_filename": "previous.jpg"}

    with pytest.raises(RuntimeError, match="valid post id"):
        bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == set()
    assert images_used == set()
    assert state["last_regular_image_filename"] == "previous.jpg"
    assert not lines_used_file.exists()
    assert not images_used_file.exists()
    assert events == []


def configure_simple_quote_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    create_response: dict | None = None,
) -> tuple[set[str], set[str], dict, Path, Path, Path, Path]:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    lines_used_file = tmp_path / "lines_used.json"
    images_used_file = tmp_path / "images_used.json"
    receipt_file = tmp_path / "regular_post_receipt.json"
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "LINES_USED_FILE", lines_used_file)
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", images_used_file)
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: create_response or {"data": {"id": "950001"}})
    monkeypatch.setattr(bot, "maybe_schedule_meme_after_quote_post", lambda state, quote_post_epoch=None, **kwargs: None)
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(["Good quote."]))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: image_analysis_for_paths([image_path]))
    return set(), set(), {}, lines_used_file, images_used_file, receipt_file, lines_file


def valid_regular_receipt(**overrides: object) -> dict:
    receipt = {
        "schema_version": 1,
        "post_id": "950001",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    receipt.update(overrides)
    return receipt


@pytest.mark.parametrize(
    ("image_basename", "initial_count", "expected_count"),
    [
        ("tg_" + ("a" * 64) + ".png", 2, 0),
        ("t01.jpg", 0, 1),
    ],
)
def test_regular_receipt_reconciliation_updates_generated_spacing_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    image_basename: str,
    initial_count: int,
    expected_count: int,
) -> None:
    receipt_file = tmp_path / "regular_post_receipt.json"
    receipt = valid_regular_receipt(image_basename=image_basename)
    receipt_file.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot, "save_regular_post_protected_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
    caplog.set_level(logging.INFO, logger=bot.log.name)
    lines_used: set[str] = set()
    images_used: set[str] = set()
    state = {"original_regular_posts_since_generated_image": initial_count}

    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is True
    assert state["original_regular_posts_since_generated_image"] == expected_count
    assert image_basename in images_used
    assert not receipt_file.exists()
    assert "GENERATED_IMAGE_SPACING_STATE_UPDATED" in caplog.text

    caplog.clear()
    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is False
    assert state["original_regular_posts_since_generated_image"] == expected_count
    assert "GENERATED_IMAGE_SPACING_STATE_UPDATED" not in caplog.text


def test_regular_receipt_reapply_does_not_double_increment_original_spacing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    receipt = valid_regular_receipt(image_basename="t01.jpg")
    lines_used: set[str] = set()
    images_used: set[str] = set()
    state = {
        "last_quote_post_epoch": receipt["quote_post_epoch"],
        "last_regular_image_filename": "t01.jpg",
        "original_regular_posts_since_generated_image": 1,
    }
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.apply_regular_post_receipt(receipt, lines_used, images_used, state)

    assert state["original_regular_posts_since_generated_image"] == 1
    assert "GENERATED_IMAGE_SPACING_STATE_UPDATED" not in caplog.text


def test_regular_receipt_reapply_initialises_missing_spacing_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_basename = "tg_" + ("a" * 64) + ".png"
    receipt = valid_regular_receipt(image_basename=image_basename)
    lines_used: set[str] = set()
    images_used: set[str] = set()
    state = {
        "last_quote_post_epoch": receipt["quote_post_epoch"],
        "last_regular_image_filename": image_basename,
    }
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)

    bot.apply_regular_post_receipt(receipt, lines_used, images_used, state)

    assert state["original_regular_posts_since_generated_image"] == 0


def test_regular_receipt_reapply_corrects_generated_spacing_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_basename = "tg_" + ("a" * 64) + ".png"
    receipt = valid_regular_receipt(image_basename=image_basename)
    lines_used: set[str] = set()
    images_used: set[str] = set()
    state = {
        "last_quote_post_epoch": receipt["quote_post_epoch"],
        "last_regular_image_filename": image_basename,
        "original_regular_posts_since_generated_image": 2,
    }
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)

    bot.apply_regular_post_receipt(receipt, lines_used, images_used, state)

    assert state["original_regular_posts_since_generated_image"] == 0


@pytest.mark.parametrize("failure", ["save_state", "quote_history", "image_history"])
def test_confirmed_regular_post_receipt_recovers_local_persistence_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    lines_used, images_used, state, lines_used_file, images_used_file, receipt_file, lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    quote_hash = bot.quote_text_hash("Good quote.")
    original_save_state = bot.save_state
    original_save_quote = bot.save_quote_used_hashes
    original_save_image = bot.save_image_used_basenames

    if failure == "save_state":
        monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: (_ for _ in ()).throw(OSError("state write failed")))
    elif failure == "quote_history":
        monkeypatch.setattr(bot, "save_quote_used_hashes", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("quote history failed")))
        monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    else:
        monkeypatch.setattr(bot, "save_image_used_basenames", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("image history failed")))
        monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    assert receipt_file.exists()
    assert quote_hash in lines_used
    assert "t01.jpg" in images_used

    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None if failure != "save_state" else original_save_state(state, **kwargs))
    monkeypatch.setattr(bot, "save_quote_used_hashes", original_save_quote)
    monkeypatch.setattr(bot, "save_image_used_basenames", original_save_image)

    reconciled_lines: set[str] = set()
    reconciled_images: set[str] = set()
    reconciled_state: dict = {}
    assert bot.reconcile_regular_post_receipt(reconciled_lines, reconciled_images, reconciled_state) is True
    assert not receipt_file.exists()
    assert json.loads(lines_used_file.read_text(encoding="utf-8")) == [quote_hash]
    assert json.loads(images_used_file.read_text(encoding="utf-8")) == ["t01.jpg"]
    assert quote_hash in reconciled_lines
    assert "t01.jpg" in reconciled_images

    lines_file.write_text("Good quote.\nNew quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(["Good quote.", "New quote."]))
    candidates = bot.quote_candidates_for_current_cycle(reconciled_lines)
    assert [candidate["text"] for candidate in candidates] == ["New quote."]


def test_receipt_write_failure_leaves_confirmed_assets_marked_in_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    quote_hash = bot.quote_text_hash("Good quote.")
    monkeypatch.setattr(bot, "write_regular_post_receipt", lambda receipt: (_ for _ in ()).throw(OSError("receipt failed")))

    with pytest.raises(RuntimeError, match="failed local recovery receipt"):
        bot.post_random_quote(lines_used, images_used, state)

    assert quote_hash in lines_used
    assert "t01.jpg" in images_used
    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]


@pytest.mark.parametrize(
    "failed_components",
    [
        {"quote_history"},
        {"image_history"},
        {"state"},
        {"quote_history", "image_history"},
        {"quote_history", "state"},
        {"image_history", "state"},
        {"quote_history", "image_history", "state"},
    ],
)
def test_receipt_write_failure_emergency_persistence_attempts_all_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_components: set[str],
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    attempts: list[str] = []
    monkeypatch.setattr(bot, "write_regular_post_receipt", lambda receipt: (_ for _ in ()).throw(OSError("receipt failed")))

    def maybe_fail(name: str) -> None:
        attempts.append(name)
        if name in failed_components:
            raise OSError(f"{name} failed")

    monkeypatch.setattr(bot, "save_quote_used_hashes", lambda *args, **kwargs: maybe_fail("quote_history"))
    monkeypatch.setattr(bot, "save_image_used_basenames", lambda *args, **kwargs: maybe_fail("image_history"))
    monkeypatch.setattr(bot, "save_state", lambda *args, **kwargs: maybe_fail("state"))

    with pytest.raises(RuntimeError) as excinfo:
        bot.post_random_quote(lines_used, images_used, state)

    assert attempts == ["quote_history", "image_history", "state"]
    for name in failed_components:
        assert name in str(excinfo.value)
    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]


@pytest.mark.parametrize("helper", ["cache_tweet", "record_recent_own_post"])
def test_regular_post_helper_failure_after_confirmation_leaves_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    helper: str,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(bot, helper, lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(f"{helper} failed")))

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    assert receipt_file.exists()
    assert json.loads(receipt_file.read_text(encoding="utf-8"))["post_id"] == "950001"
    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]


def test_regular_post_does_not_call_mutating_schedule_helpers_after_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(bot, "schedule_next_quote_post", lambda *args, **kwargs: pytest.fail("schedule_next_quote_post should not be called"))
    monkeypatch.setattr(bot, "maybe_schedule_meme_after_quote_post", lambda *args, **kwargs: pytest.fail("maybe_schedule_meme_after_quote_post should not be called"))

    bot.post_random_quote(lines_used, images_used, state)

    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]


def test_existing_valid_receipt_is_reconciled_before_next_regular_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, lines_used_file, _images_used_file, receipt_file, lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    old_hash = bot.quote_text_hash("Old quote.")
    receipt = {
        "schema_version": 1,
        "post_id": "940001",
        "quote_hash": old_hash,
        "line_no": 0,
        "source_line_number": 1,
        "text": "Old quote.",
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
    }
    bot.atomic_write_json(receipt_file, receipt)
    lines_file.write_text("Old quote.\nGood quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(["Old quote.", "Good quote."]))

    bot.post_random_quote(lines_used, images_used, state)

    assert not receipt_file.exists()
    assert old_hash in json.loads(lines_used_file.read_text(encoding="utf-8"))
    assert receipt_file.read_text(encoding="utf-8") if receipt_file.exists() else True


def test_receipt_is_not_removed_when_protected_durable_save_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt = {
        "schema_version": 1,
        "post_id": "950001",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    bot.atomic_write_json(receipt_file, receipt)
    monkeypatch.setattr(bot, "save_image_used_basenames", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("image save failed")))

    with pytest.raises(OSError):
        bot.reconcile_regular_post_receipt(lines_used, images_used, state)

    assert receipt_file.exists()


def test_protected_durable_saves_complete_before_receipt_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    calls: list[str] = []
    receipt = {
        "schema_version": 1,
        "post_id": "950001",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    bot.atomic_write_json(receipt_file, receipt)
    monkeypatch.setattr(bot, "save_quote_used_hashes", lambda *args, **kwargs: calls.append("quote"))
    monkeypatch.setattr(bot, "save_image_used_basenames", lambda *args, **kwargs: calls.append("image"))
    monkeypatch.setattr(bot, "save_state", lambda *args, **kwargs: calls.append("state"))
    monkeypatch.setattr(bot, "remove_regular_post_receipt", lambda: calls.append("remove"))

    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is True
    assert calls == ["quote", "image", "state", "remove"]


def test_malformed_receipt_blocks_new_regular_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt_file.write_text("{bad json", encoding="utf-8")

    with pytest.raises(bot.InvalidRegularPostReceipt):
        bot.post_random_quote(lines_used, images_used, state)

    assert receipt_file.exists()
    assert lines_used == set()
    assert images_used == set()


@pytest.mark.parametrize(
    "patch",
    [
        {"post_id": ""},
        {"post_id": "not-a-number"},
        {"quote_hash": "abc"},
        {"quote_post_epoch": "soon"},
        {"quote_post_epoch": 1},
        {"next_quote_post_epoch": "soon"},
        {"next_quote_post_epoch": 1_800_000_000},
        {"next_quote_post_epoch": 1_799_999_999},
        {"text": ""},
        {"text": None},
        {"image_basename": "../t01.jpg"},
        {"text": "Different text"},
        {"next_meme_post_epoch": 1_799_999_999, "next_meme_schedule_date": "2027-01-15", "next_meme_schedule_mode": "fallback"},
        {"next_meme_post_epoch": 1_800_086_400, "next_meme_schedule_date": "2000-01-01", "next_meme_schedule_mode": "fallback"},
        {"next_meme_post_epoch": 1_800_086_400, "next_meme_schedule_date": "2027-01-16", "next_meme_schedule_mode": "surprise"},
        {
            "next_meme_post_epoch": 1_800_000_600,
            "next_meme_schedule_date": "2027-01-15",
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "meme_anchor_quote_post_epoch": 1,
        },
        {
            "next_meme_post_epoch": 1_800_000_600,
            "next_meme_schedule_date": "2027-01-15",
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "meme_anchor_quote_post_epoch": 0,
            "meme_schedule_changed_by_quote": False,
        },
        {
            "next_meme_post_epoch": 1_800_000_000,
            "next_meme_schedule_date": bot.epoch_date_str(1_800_000_000),
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "meme_anchor_quote_post_epoch": 1_800_000_000,
            "meme_schedule_changed_by_quote": False,
        },
    ],
)
def test_semantically_invalid_receipts_block_main_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patch: dict,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt = {
        "schema_version": 1,
        "post_id": "950001",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    receipt.update(patch)
    bot.atomic_write_json(receipt_file, receipt)

    with pytest.raises(bot.InvalidRegularPostReceipt):
        bot.post_random_quote(lines_used, images_used, state)


def test_regular_receipt_accepts_all_runtime_meme_schedule_modes() -> None:
    next_meme_epoch = 1_800_086_400
    for mode in sorted(bot.MEME_SCHEDULE_MODES - {"", "after_first_quote_after_midday"}):
        receipt = valid_regular_receipt(
            next_meme_post_epoch=next_meme_epoch,
            next_meme_schedule_mode=mode,
            next_meme_schedule_date=bot.epoch_date_str(next_meme_epoch),
            meme_anchor_quote_post_epoch=0,
            meme_schedule_changed_by_quote=False,
        )
        assert bot.regular_post_receipt_is_semantically_valid(receipt), mode


def test_receipt_validators_reject_boolean_schema_versions_and_fractional_epochs() -> None:
    regular = valid_regular_receipt()
    assert bot.regular_post_receipt_is_semantically_valid({**regular, "schema_version": True}) is False
    assert bot.regular_post_receipt_is_semantically_valid({**regular, "quote_post_epoch": 1_800_000_000.5}) is False

    meme = {
        "schema_version": 1,
        "post_id": "950001",
        "meme_basename": "meme.png",
        "meme_post_epoch": 1_800_000_000,
        "next_meme_post_epoch": 1_800_086_400,
        "next_meme_schedule_mode": "fallback",
    }
    assert bot.meme_post_receipt_is_semantically_valid({**meme, "schema_version": True}) is False
    assert bot.meme_post_receipt_is_semantically_valid({**meme, "meme_post_epoch": 1_800_000_000.5}) is False

    reply = {
        "schema_version": 1,
        "target_id": "100",
        "reply_post_id": "900000",
        "author_id": "200",
        "reply_epoch": 1_800_000_000,
        "candidate_source": "mention",
        "reply_text": "A reply.",
    }
    assert bot.confirmed_reply_receipt_is_semantically_valid({**reply, "schema_version": True}) is False
    assert bot.confirmed_reply_receipt_is_semantically_valid({**reply, "reply_epoch": 1_800_000_000.5}) is False


def test_regular_receipt_distinguishes_quote_created_and_preserved_meme_schedules() -> None:
    quote_epoch = 1_800_000_000
    quote_created = valid_regular_receipt(
        quote_post_epoch=quote_epoch,
        next_quote_post_epoch=quote_epoch + 7200,
        next_meme_post_epoch=quote_epoch + 3600,
        next_meme_schedule_mode="after_first_quote_after_midday",
        next_meme_schedule_date=bot.epoch_date_str(quote_epoch),
        meme_anchor_quote_post_epoch=quote_epoch,
        meme_schedule_changed_by_quote=True,
    )
    assert bot.regular_post_receipt_is_semantically_valid(quote_created)

    anchor_epoch = quote_epoch - 7200
    preserved_overdue = valid_regular_receipt(
        quote_post_epoch=quote_epoch,
        next_quote_post_epoch=quote_epoch + 7200,
        next_meme_post_epoch=quote_epoch - 1800,
        next_meme_schedule_mode="after_first_quote_after_midday",
        next_meme_schedule_date=bot.epoch_date_str(anchor_epoch),
        meme_anchor_quote_post_epoch=anchor_epoch,
        meme_schedule_changed_by_quote=False,
    )
    assert bot.regular_post_receipt_is_semantically_valid(preserved_overdue)


def test_cross_midnight_first_quote_meme_anchor_is_not_rescheduled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "MEME_TRIGGER_AFTER_HOUR", 12)
    quote_one = int(datetime(2026, 7, 6, 23, 30).timestamp())
    quote_two = int(datetime(2026, 7, 6, 23, 50).timestamp())
    state = {"next_meme_post_epoch": 0, "last_meme_post_epoch": 0}

    fields = bot.meme_schedule_fields_after_quote_post(state, quote_one, delay=3600)
    assert fields["next_meme_schedule_mode"] == "after_first_quote_after_midday"
    assert fields["next_meme_schedule_date"] == "2026-07-06"
    assert bot.epoch_date_str(fields["next_meme_post_epoch"]) == "2026-07-07"
    state.update(fields)

    assert bot.meme_schedule_fields_after_quote_post(state, quote_two, delay=1800) == {}
    assert state["meme_anchor_quote_post_epoch"] == quote_one
    assert state["next_meme_post_epoch"] == quote_one + 3600


def test_regular_receipt_validates_cross_midnight_quote_anchored_meme_schedule() -> None:
    quote_epoch = int(datetime(2026, 7, 6, 23, 30).timestamp())
    receipt = valid_regular_receipt(
        quote_post_epoch=quote_epoch,
        next_quote_post_epoch=quote_epoch + 7200,
        next_meme_post_epoch=quote_epoch + 3600,
        next_meme_schedule_mode="after_first_quote_after_midday",
        next_meme_schedule_date="2026-07-06",
        meme_anchor_quote_post_epoch=quote_epoch,
        meme_schedule_changed_by_quote=True,
    )
    assert bot.regular_post_receipt_is_semantically_valid(receipt)


def test_delayed_meme_schedule_modes_clear_stale_quote_anchor() -> None:
    delayed_modes = {
        "delayed_runtime_control",
        "delayed_recent_quote",
        "delayed_write_api_cooldown",
        "delayed_api_error",
        "delayed_exception",
    }
    for mode in delayed_modes:
        state = {
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "meme_anchor_quote_post_epoch": 1_800_000_000,
        }
        target_epoch = 1_800_010_000
        bot.set_meme_delay_schedule(state, epoch=target_epoch, mode=mode, save=False)
        assert state["next_meme_post_epoch"] == target_epoch
        assert state["next_meme_schedule_mode"] == mode
        assert state["next_meme_schedule_date"] == bot.epoch_date_str(target_epoch)
        assert state["meme_anchor_quote_post_epoch"] == 0


def test_all_meme_schedule_modes_preserve_anchor_invariants() -> None:
    target_epoch = 1_800_010_000
    anchor_epoch = int(datetime(2026, 7, 6, 13, 0).timestamp())
    fallback_modes = bot.MEME_SCHEDULE_MODES - {
        "",
        "after_first_quote_after_midday",
        "delayed_runtime_control",
        "delayed_recent_quote",
        "delayed_write_api_cooldown",
        "delayed_api_error",
        "delayed_exception",
    }
    delayed_modes = {
        "delayed_runtime_control",
        "delayed_recent_quote",
        "delayed_write_api_cooldown",
        "delayed_api_error",
        "delayed_exception",
    }

    for mode in fallback_modes:
        fields = bot.next_meme_schedule_fields({}, target_epoch - 1, mode=mode)
        assert fields["next_meme_schedule_mode"] == mode
        assert fields["meme_anchor_quote_post_epoch"] == 0
        assert fields["next_meme_schedule_date"] == bot.epoch_date_str(fields["next_meme_post_epoch"])

    for mode in delayed_modes:
        fields = bot.meme_delay_schedule_fields(target_epoch, mode)
        assert fields["next_meme_schedule_mode"] == mode
        assert fields["meme_anchor_quote_post_epoch"] == 0
        assert fields["next_meme_schedule_date"] == bot.epoch_date_str(target_epoch)

    quote_fields = bot.meme_schedule_fields_after_quote_post({"last_meme_post_epoch": 0}, anchor_epoch, delay=3600)
    assert quote_fields["next_meme_schedule_mode"] == "after_first_quote_after_midday"
    assert quote_fields["meme_anchor_quote_post_epoch"] == anchor_epoch
    assert quote_fields["next_meme_schedule_date"] == bot.epoch_date_str(anchor_epoch)


def test_delayed_schedule_followed_by_regular_quote_produces_self_validating_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.set_meme_delay_schedule(state, epoch=1_800_010_000, mode="delayed_recent_quote", save=False)
    receipts: list[dict] = []

    def capture_receipt(receipt: dict) -> None:
        assert bot.regular_post_receipt_is_semantically_valid(receipt)
        receipts.append(json.loads(json.dumps(receipt)))

    monkeypatch.setattr(bot, "write_regular_post_receipt", capture_receipt)

    bot.post_random_quote(lines_used, images_used, state)

    assert receipts
    assert receipts[0]["next_meme_schedule_mode"] == "delayed_recent_quote"
    assert receipts[0]["meme_anchor_quote_post_epoch"] == 0
    assert receipts[0]["meme_schedule_changed_by_quote"] is False


@pytest.mark.parametrize(
    "patch",
    [
        {"next_meme_post_epoch": "banana"},
        {
            "next_meme_post_epoch": 1_800_086_400,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": bot.epoch_date_str(1_800_086_400),
            "meme_anchor_quote_post_epoch": "banana",
            "meme_schedule_changed_by_quote": False,
        },
        {
            "next_meme_post_epoch": 1_800_086_400,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": bot.epoch_date_str(1_800_086_400),
            "meme_anchor_quote_post_epoch": 0,
            "meme_schedule_changed_by_quote": "false",
        },
    ],
)
def test_regular_receipt_malformed_optional_fields_are_invalid_not_exceptions(patch: dict) -> None:
    receipt = valid_regular_receipt(**patch)
    assert bot.regular_post_receipt_is_semantically_valid(receipt) is False


def test_valid_receipt_reconciles_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt = {
        "schema_version": 1,
        "post_id": "950001",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    bot.atomic_write_json(receipt_file, receipt)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    context_calls = []
    def context(**kwargs):
        assert receipt_file.exists(), "startup reconciliation must retain the receipt through context dispatch"
        context_calls.append(kwargs)
        return {"status": "completed"}
    monkeypatch.setattr(bot, "maybe_post_historical_context_reply", context)

    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is True
    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is False
    assert len(context_calls) == 1
    assert lines_used == {bot.quote_text_hash("Good quote.")}
    assert images_used == {"t01.jpg"}


def test_old_receipt_does_not_move_main_post_state_behind_newer_meme(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _lines_used, _images_used, _state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt = {
        "schema_version": 1,
        "post_id": "940002",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    bot.atomic_write_json(receipt_file, receipt)
    state = {
        "last_main_post_id": "960001",
        "last_meme_post_epoch": 1_800_000_200,
        "next_meme_post_epoch": 1_800_100_000,
        "next_meme_schedule_mode": "fallback_future",
        "next_meme_schedule_date": "2027-01-18",
    }
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)

    assert bot.reconcile_regular_post_receipt(set(), set(), state) is True
    assert state["last_main_post_id"] == "960001"
    assert state["next_meme_post_epoch"] == 1_800_100_000
    assert state["next_meme_schedule_mode"] == "fallback_future"


def test_post_next_meme_blocked_by_unresolved_regular_post_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _lines_used, _images_used, _state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.atomic_write_json(
        receipt_file,
        {
            "schema_version": 1,
            "post_id": "950001",
            "quote_hash": bot.quote_text_hash("Good quote."),
            "image_basename": "t01.jpg",
            "quote_post_epoch": 1_800_000_000,
            "next_quote_post_epoch": 1_800_007_200,
            "text": "Good quote.",
        },
    )

    with pytest.raises(bot.UnresolvedRegularPostReceipt):
        bot.post_next_meme({})


def test_regular_receipt_durable_write_uses_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    receipt_file = tmp_path / "regular_post_receipt.json"
    calls: list[str] = []
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot.os, "fsync", lambda fd: calls.append("fsync"))

    bot.write_regular_post_receipt(
        valid_regular_receipt()
    )

    assert receipt_file.exists()
    assert len(calls) >= 2


def test_receipt_writers_self_validate_before_durable_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    regular_receipt_file = tmp_path / "regular_post_receipt.json"
    meme_receipt_file = tmp_path / "meme_post_receipt.json"
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", regular_receipt_file)
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", meme_receipt_file)

    with pytest.raises(RuntimeError, match="semantic validation"):
        bot.write_regular_post_receipt(valid_regular_receipt(next_meme_post_epoch="banana"))
    assert not regular_receipt_file.exists()

    valid_regular_forms = [
        valid_regular_receipt(),
        valid_regular_receipt(
            next_meme_post_epoch=1_800_086_400,
            next_meme_schedule_mode="fallback",
            next_meme_schedule_date=bot.epoch_date_str(1_800_086_400),
            meme_anchor_quote_post_epoch=0,
            meme_schedule_changed_by_quote=False,
        ),
        valid_regular_receipt(
            next_meme_post_epoch=1_800_003_600,
            next_meme_schedule_mode="after_first_quote_after_midday",
            next_meme_schedule_date=bot.epoch_date_str(1_800_000_000),
            meme_anchor_quote_post_epoch=1_800_000_000,
            meme_schedule_changed_by_quote=True,
        ),
    ]
    for receipt in valid_regular_forms:
        assert bot.regular_post_receipt_is_semantically_valid(receipt)
        bot.write_regular_post_receipt(receipt)
        assert bot.load_regular_post_receipt()[0] == "valid"
        regular_receipt_file.unlink()

    for bad_schema in [{}, {"schema_version": 2}, {"schema_version": "1"}]:
        receipt = valid_regular_receipt()
        receipt.update(bad_schema)
        if "schema_version" not in bad_schema:
            receipt.pop("schema_version", None)
        assert not bot.regular_post_receipt_is_semantically_valid(receipt)
        with pytest.raises(RuntimeError, match="semantic validation"):
            bot.write_regular_post_receipt(receipt)
        assert not regular_receipt_file.exists()

    with pytest.raises(RuntimeError, match="semantic validation"):
        bot.write_meme_post_receipt(
            {
                "schema_version": 1,
                "post_id": "970001",
                "meme_basename": "001_meme.png",
                "meme_post_epoch": 1_800_000_000,
                "next_meme_post_epoch": "banana",
            }
    )
    assert not meme_receipt_file.exists()

    valid_meme_receipt = {
        "schema_version": 1,
        "post_id": "970001",
        "meme_basename": "001_meme.png",
        "meme_post_epoch": 1_800_000_000,
        "next_meme_post_epoch": 1_800_086_400,
        "next_meme_schedule_mode": "fallback",
    }
    assert bot.meme_post_receipt_is_semantically_valid(valid_meme_receipt)
    bot.write_meme_post_receipt(valid_meme_receipt)
    assert bot.load_meme_post_receipt()[0] == "valid"
    meme_receipt_file.unlink()

    for bad_schema in [{}, {"schema_version": 2}, {"schema_version": "1"}]:
        receipt = dict(valid_meme_receipt)
        receipt.update(bad_schema)
        if "schema_version" not in bad_schema:
            receipt.pop("schema_version", None)
        assert not bot.meme_post_receipt_is_semantically_valid(receipt)
        with pytest.raises(RuntimeError, match="semantic validation"):
            bot.write_meme_post_receipt(receipt)
        assert not meme_receipt_file.exists()


def test_load_image_used_basenames_empty_scan_preserves_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    history = tmp_path / "images_used.json"
    history.write_text(json.dumps(["t01.jpg"]) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", history)
    before = history.read_text(encoding="utf-8")

    assert bot.load_image_used_basenames([]) == {"t01.jpg"}
    assert history.read_text(encoding="utf-8") == before


def test_image_history_preserves_missing_basenames_and_reuses_when_file_reappears(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    t01 = image_dir / "t01.jpg"
    t01.write_bytes(b"one")
    history = tmp_path / "images_used.json"
    history.write_text(json.dumps(["t01.jpg", "temporarily_missing.jpg"]) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", history)

    used = bot.load_image_used_basenames([str(t01)])
    assert used == {"t01.jpg", "temporarily_missing.jpg"}
    assert "temporarily_missing.jpg" not in bot.available_image_basenames([str(t01)], used, {})[0]

    missing = image_dir / "temporarily_missing.jpg"
    missing.write_bytes(b"two")
    used = bot.load_image_used_basenames([str(t01), str(missing)])
    assert "temporarily_missing.jpg" in used


def test_partial_visible_image_scan_does_not_migrate_legacy_indices_or_rewrite_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    visible = []
    for name in ["t40.jpg", "t41.jpg", "t42.jpg"]:
        path = image_dir / name
        path.write_bytes(name.encode("utf-8"))
        visible.append(path)
    expected = visible + [image_dir / "t43.jpg"]
    expected[-1].write_bytes(b"hidden")
    analysis = image_analysis_for_paths(expected)
    expected[-1].unlink()
    history = tmp_path / "images_used.json"
    history.write_text("[0, 1, 2]\n", encoding="utf-8")
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", history)
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)
    before = history.read_text(encoding="utf-8")

    used = bot.load_image_used_basenames([str(path) for path in visible])

    assert used == {0, 1, 2}
    assert history.read_text(encoding="utf-8") == before


def test_regular_posting_blocks_unsafe_legacy_image_index_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    visible = image_dir / "t40.jpg"
    hidden = image_dir / "t41.jpg"
    visible.write_bytes(b"visible")
    hidden.write_bytes(b"hidden")
    analysis = image_analysis_for_paths([visible, hidden])
    hidden.unlink()
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)
    used = {0}

    with pytest.raises(bot.UnsafeImageHistoryMigration):
        bot.choose_matched_unused_image(used, {"analysis": {"primary_topics": []}}, {})

    assert used == {0}


def test_unsafe_first_quote_index_migration_is_preserved_and_blocks_posting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = ["First quote.", "Second quote."]
    reordered = ["Inserted quote.", "First quote.", "Second quote."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(reordered) + "\n", encoding="utf-8")
    history = tmp_path / "lines_used.json"
    history.write_text("[1]\n", encoding="utf-8")
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "LINES_USED_FILE", history)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(original))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: image_analysis_for_paths([image_path]))

    used = bot.load_quote_used_hashes([line + "\n" for line in reordered])

    assert used == {1}
    assert history.read_text(encoding="utf-8") == "[1]\n"
    with pytest.raises(bot.CorruptUsedHistoryError):
        bot.post_random_quote(used, set(), {})


def test_corrupt_current_used_history_does_not_reduce_to_stale_pickle(tmp_path: Path) -> None:
    json_path = tmp_path / "lines_used.json"
    pickle_path = tmp_path / "lines_used.pickle"
    json_path.write_text("{bad", encoding="utf-8")
    pickle_path.write_bytes(b"legacy pickle no longer trusted")

    with pytest.raises(bot.CorruptUsedHistoryError):
        bot.load_used_set(json_path, legacy_pickle_path=pickle_path)


def test_quote_hash_candidates_deduplicate_identical_source_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = ["Duplicate quote.", "Another quote.", "Duplicate quote."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(lines))

    candidates = bot.quote_candidates_for_current_cycle(set())

    assert [candidate["text"] for candidate in candidates].count("Duplicate quote.") == 1


def test_posting_duplicate_quote_marks_hash_and_blocks_identical_line_same_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    lines = ["Duplicate quote.", "Another quote.", "Duplicate quote."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "LINES_USED_FILE", tmp_path / "lines_used.json")
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", tmp_path / "images_used.json")
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", tmp_path / "regular_post_receipt.json")
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(lines))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: image_analysis_for_paths([image_path]))
    monkeypatch.setattr(bot.random, "uniform", lambda low, high: low)
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "950001"}})
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "maybe_schedule_meme_after_quote_post", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    lines_used: set[str] = set()
    bot.post_random_quote(lines_used, set(), {})

    duplicate_hash = bot.quote_text_hash("Duplicate quote.")
    assert lines_used == {duplicate_hash}
    candidates = bot.quote_candidates_for_current_cycle(lines_used)
    assert [candidate["text"] for candidate in candidates] == ["Another quote."]


def test_quote_hash_history_migrates_legacy_lines_and_survives_reordering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_lines = ["First quote.", "Second quote."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(original_lines) + "\n", encoding="utf-8")
    history = tmp_path / "lines_used.json"
    history.write_text("[1]\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "LINES_USED_FILE", history)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(original_lines))

    used = bot.load_quote_used_hashes([line + "\n" for line in original_lines])
    second_hash = bot.quote_text_hash("Second quote.")
    assert used == {second_hash}

    reordered = ["Second quote.", "First quote.", "Brand new quote."]
    lines_file.write_text("\n".join(reordered) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(reordered))
    candidates = bot.quote_candidates_for_current_cycle(used)
    assert "Second quote." not in [candidate["text"] for candidate in candidates]
    assert "Brand new quote." in [candidate["text"] for candidate in candidates]


def test_image_metadata_is_used_only_when_current_content_matches(tmp_path: Path) -> None:
    image_path = tmp_path / "t01.jpg"
    image_path.write_bytes(b"original")
    analysis = image_analysis_for_paths([image_path], {"t01.jpg": {"description": "Original metadata"}})

    _, metadata = bot.image_metadata_for_basename(analysis, "t01.jpg", str(image_path))
    assert metadata == {"description": "Original metadata"}

    image_path.write_bytes(b"changed")
    with pytest.raises(bot.StaleImageMetadata):
        bot.image_metadata_for_basename(analysis, "t01.jpg", str(image_path))


def test_image_hash_cache_detects_changed_bytes_with_preserved_size_and_mtime(tmp_path: Path) -> None:
    image_path = tmp_path / "t01.jpg"
    image_path.write_bytes(b"abcdef")
    first = bot.current_image_sha256(str(image_path))
    stat = image_path.stat()
    image_path.write_bytes(b"ghijkl")
    os.utime(image_path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    second = bot.current_image_sha256(str(image_path))

    assert second != first


def test_stale_image_is_skipped_while_valid_image_remains_selectable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    stale_path = image_dir / "t01.jpg"
    valid_path = image_dir / "t02.jpg"
    stale_path.write_bytes(b"original")
    valid_path.write_bytes(b"valid")
    analysis = image_analysis_for_paths([stale_path, valid_path])
    stale_path.write_bytes(b"changed")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)

    chosen = bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["anything"]}}, {})

    assert chosen["basename"] == "t02.jpg"


def test_new_unanalysed_image_is_excluded_while_valid_image_remains_selectable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    used_path = image_dir / "t01.jpg"
    valid_path = image_dir / "t02.jpg"
    new_path = image_dir / "t03.jpg"
    used_path.write_bytes(b"used")
    valid_path.write_bytes(b"valid")
    analysis = image_analysis_for_paths([used_path, valid_path])
    new_path.write_bytes(b"new")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)

    chosen = bot.choose_matched_unused_image({"t01.jpg"}, {"analysis": {"primary_topics": ["anything"]}}, {})

    assert chosen["basename"] == "t02.jpg"


def test_all_stale_images_fail_without_mutating_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"original")
    analysis = image_analysis_for_paths([image_path])
    image_path.write_bytes(b"changed")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)
    used: set[str] = set()

    with pytest.raises(bot.NoEligibleImageForQuote):
        bot.choose_matched_unused_image(used, {"analysis": {"primary_topics": ["anything"]}}, {})

    assert used == set()


def test_missing_whole_quote_analysis_fails_regular_quote_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Christmas quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: None)

    with pytest.raises(RuntimeError, match="Quote analysis unavailable"):
        bot.choose_unused_line_candidate(set())


def test_unanalysed_current_christmas_quote_is_skipped_in_july(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = ["Ordinary quote.", "Christmas quote edited."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    analysed = quote_analysis_for_lines(["Ordinary quote.", "Old Christmas quote."])
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: analysed)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))

    candidates = bot.quote_candidates_for_current_cycle(set())

    assert [candidate["text"] for candidate in candidates] == ["Ordinary quote."]


def test_missing_image_analysis_fails_regular_image_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: None)

    with pytest.raises(bot.NoEligibleImageForQuote, match="Image analysis unavailable"):
        bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["government"]}}, {})


def test_successful_meme_post_persists_post_and_future_schedule_in_one_state_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    meme_path = meme_dir / "001_meme.png"
    meme_path.write_bytes(b"meme")
    saved_states: list[dict] = []
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "970001"}})
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: saved_states.append(json.loads(json.dumps(state))))
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    bot.post_next_meme(state)

    assert len(saved_states) == 1
    saved = saved_states[0]
    assert saved["last_main_post_id"] == "970001"
    assert saved["last_meme_post_epoch"] == 1_800_000_000
    assert "001_meme.png" in saved["posted_meme_filenames"]
    assert saved["next_meme_post_epoch"] > 1_800_000_000


@pytest.mark.parametrize("helper", ["cache_tweet", "record_recent_own_post"])
def test_meme_helper_failure_after_confirmation_leaves_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    helper: str,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "970001"}})
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, helper, lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(f"{helper} failed")))
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    assert bot.MEME_POST_RECEIPT_FILE.exists()
    assert json.loads(bot.MEME_POST_RECEIPT_FILE.read_text(encoding="utf-8"))["post_id"] == "970001"


def test_regular_post_commits_future_quote_schedule_and_receipt_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipts: list[dict] = []
    monkeypatch.setattr(bot.random, "randint", lambda low, high: 7200)
    monkeypatch.setattr(bot, "write_regular_post_receipt", lambda receipt: receipts.append(json.loads(json.dumps(receipt))))

    bot.post_random_quote(lines_used, images_used, state)

    assert state["next_quote_post_epoch"] == 1_800_007_200
    assert receipts[0]["next_quote_post_epoch"] == 1_800_007_200


def test_regular_post_context_stage_runs_only_after_durable_main_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, *_ = configure_simple_quote_post(tmp_path, monkeypatch)
    saved = {"done": False}; context_calls = []
    original_save = bot.save_regular_post_protected_state
    def tracked_save(*args, **kwargs):
        original_save(*args, **kwargs); saved["done"] = True
    def context(**kwargs):
        assert saved["done"] is True
        assert bot.REGULAR_POST_RECEIPT_FILE.exists(), "receipt must bridge a crash before context dispatch"
        context_calls.append(kwargs)
        return {"status": "completed"}
    monkeypatch.setattr(bot, "save_regular_post_protected_state", tracked_save)
    monkeypatch.setattr(bot, "maybe_post_historical_context_reply", context)
    bot.post_random_quote(lines_used, images_used, state)
    assert len(context_calls) == 1 and context_calls[0]["parent_post_id"] == "950001"


def test_context_failure_does_not_undo_confirmed_regular_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, *_ = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(bot, "maybe_post_historical_context_reply", lambda **kwargs: {"status": "failed"})
    bot.post_random_quote(lines_used, images_used, state)
    assert bot.quote_text_hash("Good quote.") in lines_used and state["last_main_post_id"] == "950001"


def test_unpersisted_context_failure_retains_main_receipt_for_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, *_ = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("context preparation failed")),
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    assert bot.REGULAR_POST_RECEIPT_FILE.exists()
    assert bot.quote_text_hash("Good quote.") in lines_used
    assert state["last_main_post_id"] == "950001"


def test_context_reply_disabled_configuration_makes_no_post(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "historical_context_reply", {**bot.historical_context_reply, "enabled": False})
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("disabled context must not post"))
    assert bot.maybe_post_historical_context_reply(
        quote_hash="a" * 64, quote_text="Quote", parent_post_id="123"
    ) == {"status": "disabled"}


def test_ambiguous_context_outcome_propagates_for_manual_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from historical_context_formatter import AmbiguousContextReplyOutcome, HistoricalContextReplyStore

    research = Path(__file__).resolve().parents[1] / "semantic_alignment_research" / "quote_research_full_001"
    packets = json.loads((research / "research_packets.json").read_text())["items"]
    packet = next(iter(packets.values()))
    monkeypatch.setattr(bot, "historical_context_reply", {**bot.historical_context_reply, "enabled": True})
    monkeypatch.setattr(bot, "HISTORICAL_CONTEXT_RESEARCH_DIR", research)
    monkeypatch.setattr(bot, "HISTORICAL_CONTEXT_REPLY_HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(bot, "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE", tmp_path / "receipt.json")
    monkeypatch.setattr(
        HistoricalContextReplyStore,
        "post",
        lambda self, **kwargs: (_ for _ in ()).throw(AmbiguousContextReplyOutcome("ambiguous")),
    )

    with pytest.raises(AmbiguousContextReplyOutcome):
        bot.maybe_post_historical_context_reply(
            quote_hash=packet["quote_id"],
            quote_text=packet["quote_text"],
            parent_post_id="123",
        )


def test_unpersisted_context_preparation_failure_propagates_for_main_receipt_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import historical_context_formatter as context_module

    monkeypatch.setattr(bot, "historical_context_reply", {**bot.historical_context_reply, "enabled": True})
    monkeypatch.setattr(
        context_module,
        "load_and_validate_corpus",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("corpus temporarily unreadable")),
    )

    with pytest.raises(RuntimeError, match="corpus temporarily unreadable"):
        bot.maybe_post_historical_context_reply(
            quote_hash="a" * 64,
            quote_text="Quote",
            parent_post_id="123",
        )


def test_regular_post_uses_confirmed_time_for_noon_meme_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    pre_confirm_epoch = int(datetime(2026, 7, 6, 11, 59, 50).timestamp())
    confirmed_epoch = int(datetime(2026, 7, 6, 12, 0, 5).timestamp())
    remote_confirmed = {"value": False}

    def fake_create_post(**kwargs: object) -> dict:
        remote_confirmed["value"] = True
        return {"data": {"id": "950001"}}

    def fake_now_epoch() -> int:
        return confirmed_epoch if remote_confirmed["value"] else pre_confirm_epoch

    def fake_randint(low: int, high: int) -> int:
        if low == bot.POST_SLEEP_MIN and high == bot.POST_SLEEP_MAX:
            return 7200
        return 3600

    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "MEME_TRIGGER_AFTER_HOUR", 12)
    monkeypatch.setattr(bot, "create_post", fake_create_post)
    monkeypatch.setattr(bot, "now_epoch", fake_now_epoch)
    monkeypatch.setattr(bot.random, "randint", fake_randint)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_quote_post_epoch"] == confirmed_epoch
    assert state["next_quote_post_epoch"] == confirmed_epoch + 7200
    assert state["next_meme_schedule_mode"] == "after_first_quote_after_midday"
    assert state["meme_anchor_quote_post_epoch"] == confirmed_epoch
    assert state["next_meme_post_epoch"] == confirmed_epoch + 3600


def test_regular_schedule_finalisation_failure_after_confirmation_is_confirmed_local_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    quote_hash = bot.quote_text_hash("Good quote.")
    monkeypatch.setattr(
        bot,
        "meme_schedule_fields_after_quote_post",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("schedule finalisation failed")),
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    assert quote_hash in lines_used
    assert "t01.jpg" in images_used
    assert state["last_main_post_id"] == "950001"
    assert not receipt_file.exists()
    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]


def test_regular_quote_schedule_failure_after_confirmation_suppresses_quote_lane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    quote_hash = bot.quote_text_hash("Good quote.")
    monkeypatch.setattr(bot.random, "randint", lambda low, high: 7200)
    monkeypatch.setattr(
        bot,
        "next_quote_schedule_fields",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("quote schedule failed")),
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    assert quote_hash in lines_used
    assert "t01.jpg" in images_used
    assert state["last_main_post_id"] == "950001"
    assert state["last_quote_post_epoch"] == 1_800_000_000
    assert state["next_quote_post_epoch"] == 1_800_007_200
    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]
    assert not receipt_file.exists()


def test_regular_receipt_replay_restores_future_quote_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt = {
        "schema_version": 1,
        "post_id": "950001",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    bot.atomic_write_json(receipt_file, receipt)

    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is True
    assert state["next_quote_post_epoch"] == 1_800_007_200


def test_regular_receipt_replay_does_not_create_second_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt = {
        "schema_version": 1,
        "post_id": "950001",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    bot.atomic_write_json(receipt_file, receipt)
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called after receipt replay"))

    bot.post_random_quote(lines_used, images_used, state)

    assert not receipt_file.exists()
    assert state["next_quote_post_epoch"] == 1_800_007_200


def test_regular_post_state_failure_leaves_receipt_with_future_quote_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(bot.random, "randint", lambda low, high: 7200)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: (_ for _ in ()).throw(OSError("state failed")))

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert receipt["next_quote_post_epoch"] == 1_800_007_200
    assert state["next_quote_post_epoch"] == 1_800_007_200


def test_regular_receipt_removal_failure_keeps_future_quote_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(bot.random, "randint", lambda low, high: 7200)
    monkeypatch.setattr(bot, "remove_regular_post_receipt", lambda: (_ for _ in ()).throw(OSError("remove failed")))

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    assert state["next_quote_post_epoch"] == 1_800_007_200
    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]


def test_regular_post_invalid_non_numeric_post_id_restores_histories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(
        tmp_path,
        monkeypatch,
        create_response={"data": {"id": "banana"}},
    )

    with pytest.raises(RuntimeError, match="valid post id"):
        bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == set()
    assert images_used == set()
    assert "last_main_post_id" not in state


@pytest.mark.parametrize(
    ("failure", "expected_exception"),
    [
        ("global_image", bot.GlobalImageUnavailable),
        ("unsafe_image_migration", bot.UnsafeImageHistoryMigration),
        ("unexpected_image", ValueError),
        ("upload", OSError),
        ("create", OSError),
        ("invalid_post_id", RuntimeError),
    ],
)
def test_pre_confirmation_failures_restore_histories_after_quote_cycle_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_exception: type[Exception],
) -> None:
    lines_used = {"old-line"}
    images_used = {"old-image"}
    state: dict = {}
    monkeypatch.setattr(bot, "reconcile_main_post_receipts", lambda *args, **kwargs: {"regular": False, "meme": False})
    monkeypatch.setattr(bot, "quote_used_history_has_legacy_indices", lambda used: False)

    def reset_then_quote(used: set, *, excluded_quote_hashes: set[str] | None = None) -> dict:
        used.clear()
        return {"line_no": 0, "quote_hash": bot.quote_text_hash("Good quote."), "text": "Good quote.", "analysis": {}}

    monkeypatch.setattr(bot, "choose_unused_line_candidate", reset_then_quote)

    if failure == "global_image":
        monkeypatch.setattr(bot, "choose_matched_unused_image", lambda *args, **kwargs: (_ for _ in ()).throw(bot.GlobalImageUnavailable("no images")))
    elif failure == "unsafe_image_migration":
        monkeypatch.setattr(bot, "choose_matched_unused_image", lambda *args, **kwargs: (_ for _ in ()).throw(bot.UnsafeImageHistoryMigration("unsafe")))
    elif failure == "unexpected_image":
        monkeypatch.setattr(bot, "choose_matched_unused_image", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("scoring failed")))
    else:
        monkeypatch.setattr(
            bot,
            "choose_matched_unused_image",
            lambda *args, **kwargs: {"image_no": 0, "path": "image.jpg", "basename": "image.jpg", "score": 1.0},
        )
        if failure == "upload":
            monkeypatch.setattr(bot, "upload_media", lambda path: (_ for _ in ()).throw(OSError("upload failed")))
        else:
            monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
            if failure == "create":
                monkeypatch.setattr(bot, "create_post", lambda **kwargs: (_ for _ in ()).throw(OSError("create failed")))
            elif failure == "invalid_post_id":
                monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "banana"}})

    with pytest.raises(expected_exception):
        bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == {"old-line"}
    assert images_used == {"old-image"}
    assert "last_main_post_id" not in state



def test_daily_meme_missing_post_id_fails_without_success_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    events: list[str] = []
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {}})
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: pytest.fail("save_state should not be called"))
    monkeypatch.setattr(bot, "log_event", lambda event, **kwargs: events.append(event))

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(RuntimeError, match="valid post id"):
        bot.post_next_meme(state)

    assert state == {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    assert events == []


def test_meme_post_uses_confirmed_time_across_midnight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)
    pre_confirm_epoch = int(datetime(2026, 7, 6, 23, 59, 50).timestamp())
    confirmed_epoch = int(datetime(2026, 7, 7, 0, 0, 5).timestamp())
    remote_confirmed = {"value": False}

    def fake_create_post(**kwargs: object) -> dict:
        remote_confirmed["value"] = True
        return {"data": {"id": "970001"}}

    def fake_now_epoch() -> int:
        return confirmed_epoch if remote_confirmed["value"] else pre_confirm_epoch

    monkeypatch.setattr(bot, "create_post", fake_create_post)
    monkeypatch.setattr(bot, "now_epoch", fake_now_epoch)

    state = {"next_meme_post_epoch": pre_confirm_epoch, "posted_meme_filenames": []}
    bot.post_next_meme(state)

    assert state["last_meme_post_epoch"] == confirmed_epoch
    assert bot.epoch_date_str(state["last_meme_post_epoch"]) == "2026-07-07"
    assert state["posted_meme_filenames"] == ["001_meme.png"]
    assert state["next_meme_schedule_mode"] == "fallback"
    assert state["next_meme_post_epoch"] > confirmed_epoch
    assert bot.epoch_date_str(state["next_meme_post_epoch"]) == "2026-07-08"


def test_meme_schedule_finalisation_failure_after_confirmation_is_confirmed_local_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "970001"}})
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(
        bot,
        "next_meme_schedule_fields",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("schedule finalisation failed")),
    )
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    assert state["last_main_post_id"] == "970001"
    assert state["last_meme_post_epoch"] == 1_800_000_000
    assert state["posted_meme_filenames"] == ["001_meme.png"]
    assert state["next_meme_post_epoch"] > state["last_meme_post_epoch"]
    assert state["meme_anchor_quote_post_epoch"] == 0
    assert not bot.MEME_POST_RECEIPT_FILE.exists()


def test_confirmed_meme_state_failure_reconciles_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    receipt_file = tmp_path / "meme_post_receipt.json"
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "970001"}})
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: (_ for _ in ()).throw(OSError("state failed")))
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    assert receipt_file.exists()
    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert receipt["next_meme_post_epoch"] > 1_800_000_000
    assert state["next_meme_post_epoch"] > state["last_meme_post_epoch"]

    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    recovered: dict = {}
    assert bot.reconcile_meme_post_receipt(recovered) is True
    assert bot.reconcile_meme_post_receipt(recovered) is False
    assert recovered["last_main_post_id"] == "970001"
    assert recovered["posted_meme_filenames"] == ["001_meme.png"]
    assert recovered["next_meme_post_epoch"] == receipt["next_meme_post_epoch"]


def test_meme_receipt_replay_does_not_create_second_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_file = tmp_path / "meme_post_receipt.json"
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", receipt_file)
    bot.atomic_write_json(
        receipt_file,
        {
            "schema_version": 1,
            "post_id": "970001",
            "meme_basename": "001_meme.png",
            "meme_post_epoch": 1_800_000_000,
            "next_meme_post_epoch": 1_800_086_400,
        },
    )
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called after meme receipt replay"))

    state: dict = {}
    bot.post_next_meme(state)

    assert not receipt_file.exists()
    assert state["posted_meme_filenames"] == ["001_meme.png"]
    assert state["next_meme_post_epoch"] == 1_800_086_400


def test_confirmed_meme_receipt_write_failure_keeps_normal_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "970001"}})
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "write_meme_post_receipt", lambda receipt: (_ for _ in ()).throw(OSError("receipt failed")))
    saved_states: list[dict] = []
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: saved_states.append(json.loads(json.dumps(state))))
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    assert state["posted_meme_filenames"] == ["001_meme.png"]
    assert state["next_meme_post_epoch"] > 1_800_000_000
    assert state["next_meme_post_epoch"] > state["last_meme_post_epoch"]
    assert saved_states[-1]["next_meme_post_epoch"] == state["next_meme_post_epoch"]
    assert state["next_meme_schedule_mode"] == "fallback"


def test_meme_receipt_removal_failure_keeps_future_meme_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path: "media-1")
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: {"data": {"id": "970001"}})
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "remove_meme_post_receipt", lambda: (_ for _ in ()).throw(OSError("remove failed")))
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    assert state["next_meme_post_epoch"] > state["last_meme_post_epoch"]
    assert state["meme_anchor_quote_post_epoch"] == 0


def test_test_post_quote_reports_confirmed_local_failure_distinctly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: {})
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda images: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(
        bot,
        "post_random_quote",
        lambda *args, **kwargs: (_ for _ in ()).throw(bot.ConfirmedPostLocalPersistenceError("confirmed")),
    )
    saved: list[dict] = []
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: saved.append(dict(state)))

    assert bot.run_test_post_quote() == 3
    assert saved == [{}]


def test_test_post_quote_migrates_old_meme_schedule_before_post_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    state = {
        "meme_schedule_version": 1,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    seen_states: list[dict] = []
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda images: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "post_random_quote", lambda lines_used, images_used, state: seen_states.append(json.loads(json.dumps(state))))

    assert bot.run_test_post_quote() == 0
    assert seen_states[0]["meme_schedule_version"] == bot.MEME_SCHEDULE_VERSION
    assert seen_states[0]["next_meme_schedule_mode"] == "fallback_migrated"


def test_test_post_quote_preserves_current_meme_schedule_preflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    state = {
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    before = json.loads(json.dumps(state))
    seen_states: list[dict] = []
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda images: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "post_random_quote", lambda lines_used, images_used, state: seen_states.append(json.loads(json.dumps(state))))

    assert bot.run_test_post_quote() == 0
    assert seen_states[0] == before


def test_test_post_quote_load_failure_happens_before_post_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: (_ for _ in ()).throw(RuntimeError("future meme schedule version")))
    monkeypatch.setattr(bot, "post_random_quote", lambda *args, **kwargs: pytest.fail("post_random_quote should not be called"))

    with pytest.raises(RuntimeError, match="future meme schedule version"):
        bot.run_test_post_quote()


def test_test_post_quote_receipt_replay_does_not_create_second_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.atomic_write_json(receipt_file, valid_regular_receipt())
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda lines: lines_used)
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda images: images_used)
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called after receipt replay"))

    assert bot.run_test_post_quote() == 0
    assert not receipt_file.exists()
    assert state["last_main_post_id"] == "950001"


def test_test_post_meme_reports_confirmed_local_failure_distinctly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: {})
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        bot,
        "post_next_meme",
        lambda *args, **kwargs: (_ for _ in ()).throw(bot.ConfirmedPostLocalPersistenceError("confirmed")),
    )
    saved: list[dict] = []
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: saved.append(dict(state)))

    assert bot.run_test_post_meme() == 3
    assert saved == [{}]


def test_test_post_meme_migrates_old_meme_schedule_before_post_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    state = {
        "meme_schedule_version": 1,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    seen_states: list[dict] = []
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "post_next_meme", lambda state: seen_states.append(json.loads(json.dumps(state))))

    assert bot.run_test_post_meme() == 0
    assert seen_states[0]["meme_schedule_version"] == bot.MEME_SCHEDULE_VERSION
    assert seen_states[0]["next_meme_schedule_mode"] == "fallback_migrated"


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("false", False),
        ("1", True),
        ("0", False),
        ("yes", True),
        ("no", False),
        ("on", True),
        ("off", False),
        ("banana", False),
        ([], False),
        ({}, False),
        (None, False),
        ("", False),
    ],
)
def test_control_bool_parses_explicit_values(value: object, expected: bool) -> None:
    assert bot.control_bool({"disable_meme_posts": value}, "disable_meme_posts") is expected


def test_control_bool_missing_value_is_false() -> None:
    assert bot.control_bool({}, "disable_meme_posts") is False


@pytest.mark.parametrize("post_id", ["123456", 123456])
def test_create_post_accepts_valid_numeric_ids(monkeypatch: pytest.MonkeyPatch, post_id: object) -> None:
    monkeypatch.setattr(bot, "x_request", lambda *args, **kwargs: {"data": {"id": post_id}})

    assert bot.create_post("hello") == {"data": {"id": post_id}}


def test_create_post_passes_long_text_without_280_character_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Historically grounded context. " * 20
    assert len(text) > 280
    requests = []
    monkeypatch.setattr(
        bot,
        "x_request",
        lambda *args, **kwargs: requests.append((args, kwargs)) or {"data": {"id": "123456"}},
    )

    bot.create_post(text, reply_to_id="654321")

    assert requests[0][0] == ("POST", "/2/tweets")
    assert requests[0][1]["json"]["text"] == text
    assert requests[0][1]["json"]["reply"] == {"in_reply_to_tweet_id": "654321"}


@pytest.mark.parametrize(
    "response",
    [
        {"data": {"id": "banana"}},
        {"data": {"id": ""}},
        {"data": {}},
        {"data": []},
        {"data": "not an object"},
        {},
    ],
)
def test_create_post_rejects_invalid_or_missing_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: dict,
) -> None:
    marker = tmp_path / "ambiguous_post.json"
    monkeypatch.setattr(bot, "AMBIGUOUS_POST_OUTCOME_FILE", marker)
    monkeypatch.setattr(bot, "x_request", lambda *args, **kwargs: response)

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.create_post("hello")

    assert json.loads(marker.read_text(encoding="utf-8"))["outcome"] == "ambiguous_remote_post"


def test_malformed_reply_post_id_is_not_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0

    mention = {
        "id": "100",
        "author_id": "200",
        "text": "@MrsMThatcher hello",
        "conversation_id": "100",
        "referenced_tweets": [],
    }

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
    monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda state: [mention])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda text: False)
    monkeypatch.setattr(bot, "build_context_for_grok", lambda mention, state: ("context", True))
    monkeypatch.setattr(bot, "ask_grok_for_reply", lambda context: "A reply.")
    monkeypatch.setattr(bot, "x_request", lambda *args, **kwargs: {"data": {"id": "banana"}})
    monkeypatch.setattr(bot, "save_state", lambda state: None)

    status = bot.maybe_reply_to_mentions(state)

    assert status == bot.NORMAL_CHECK_STATUS_API_ERROR
    assert state["daily_reply_count"] == 0
    assert state["replied_to_ids"] == []
    assert state["own_auto_reply_ids"] == []
    assert state["tweet_cache"] == {}


@pytest.mark.parametrize("candidate_source", ["mention", "hot_post_reply"])
def test_own_historical_context_reply_is_never_processed_as_incoming_reply(
    monkeypatch: pytest.MonkeyPatch,
    candidate_source: str,
) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0
    own_context_reply = {
        "id": "123456",
        "author_id": str(bot.MY_USER_ID),
        "text": "Context\nSpoken during: A speech.\n\nVerification: Exact wording",
        "conversation_id": "654321",
        "referenced_tweets": [{"type": "replied_to", "id": "654321"}],
        "_source": candidate_source,
        "_hot_original_post_id": "654321",
    }

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda state: [own_context_reply] if candidate_source == "mention" else [])
    monkeypatch.setattr(
        bot,
        "get_hot_post_reply_candidates",
        lambda state: [own_context_reply] if candidate_source == "hot_post_reply" else [],
    )
    monkeypatch.setattr(
        bot,
        "build_context_for_grok",
        lambda *args, **kwargs: pytest.fail("own context reply must not be sent to xAI"),
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda *args, **kwargs: pytest.fail("own context reply must not receive another X reply"),
    )
    monkeypatch.setattr(bot, "save_state", lambda *args, **kwargs: None)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert state["daily_reply_count"] == 0
    assert state["replied_to_ids"] == []


def test_confirmed_mention_reply_save_failure_replays_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(SCENARIOS / "normal_mention_reply.json")
    scenario["grok_replies"] = [
        "Quite right. Good sense is unfashionable only to those profiting from nonsense.",
        "Quite right. Good sense is unfashionable only to those profiting from nonsense.",
    ]
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        state_file = tmp_path / "bot_state.json"
        control_file = tmp_path / "mrsMThatcher.control.json"
        watch_file = tmp_path / "extra_quote_watch_post_ids.txt"

        monkeypatch.setattr(bot, "STATE_FILE", state_file)
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", control_file)
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", watch_file)
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
        monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        original_save_state = bot.save_state
        initial_state = bot.default_state()
        initial_state.update(
            {
                "last_seen_mention_id": "99",
                "daily_reply_date": bot.current_datetime().strftime("%Y-%m-%d"),
                "daily_reply_count": 0,
                "last_reply_epoch": 0,
                "replied_to_ids": [],
                "daily_replied_author_ids": [],
                "daily_replied_author_counts": {},
                "own_auto_reply_ids": [],
                "tweet_cache": {},
            }
        )
        original_save_state(initial_state)

        def fail_first_post_success_save(state: dict, **kwargs: object) -> None:
            if server.posts:
                raise OSError("injected post-success save failure")
            original_save_state(state, **kwargs)

        monkeypatch.setattr(bot, "save_state", fail_first_post_success_save)
        first_state = bot.load_state()

        with pytest.raises(OSError, match="injected post-success save failure"):
            bot.maybe_reply_to_mentions(first_state)

        assert len(server.posts) == 1
        first_reply_id = "900000"
        assert server.posts[0]["reply"]["in_reply_to_tweet_id"] == "100"

        durable_after_failed_save = json.loads(state_file.read_text(encoding="utf-8"))
        assert "100" not in durable_after_failed_save.get("replied_to_ids", [])
        assert durable_after_failed_save.get("last_seen_mention_id") == "99"
        assert durable_after_failed_save.get("daily_reply_count") == 0
        assert durable_after_failed_save.get("last_reply_epoch") == 0
        assert durable_after_failed_save.get("daily_replied_author_counts", {}) == {}
        assert first_reply_id not in durable_after_failed_save.get("own_auto_reply_ids", [])

        monkeypatch.setattr(bot, "save_state", original_save_state)
        restarted_state = bot.load_state()
        assert "100" not in restarted_state.get("replied_to_ids", [])
        assert restarted_state.get("last_seen_mention_id") == "99"

        second_status = bot.maybe_reply_to_mentions(restarted_state)

        assert second_status == bot.NORMAL_CHECK_STATUS_CHECKED
        assert [post["reply"]["in_reply_to_tweet_id"] for post in server.posts] == ["100"]
    finally:
        server.stop()


def test_mention_native_photo_context_reaches_xai(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = {
        "mentions": [
            {
                "id": "100",
                "text": "You've been conquered. https://t.co/example",
                "author_id": "200",
                "conversation_id": "100",
                "created_at": "2026-07-06T10:00:00Z",
                "attachments": {"media_keys": ["3_100"]},
            }
        ],
        "mentions_extra": {
            "includes": {
                "media": [
                    {
                        "media_key": "3_100",
                        "type": "photo",
                        "url": "https://pbs.twimg.com/media/native-photo.jpg",
                    }
                ]
            }
        },
        "grok_replies": ["SKIP"],
    }
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")

        assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED

        assert len(server.xai_requests) == 1
        user_content = server.xai_requests[0]["messages"][1]["content"]
        assert isinstance(user_content, list)
        text_parts = [part["text"] for part in user_content if part.get("type") == "text"]
        image_parts = [part for part in user_content if part.get("type") == "image_url"]
        assert text_parts
        assert "You've been conquered." in text_parts[0]
        assert "https://t.co/example" not in text_parts[0]
        assert image_parts == [
            {
                "type": "image_url",
                "image_url": {"url": "https://pbs.twimg.com/media/native-photo.jpg"},
            }
        ]
    finally:
        server.stop()


def test_text_only_mention_keeps_plain_xai_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = {
        "mentions": [
            {
                "id": "100",
                "text": "A plain comment.",
                "author_id": "200",
                "conversation_id": "100",
                "created_at": "2026-07-06T10:00:00Z",
            }
        ],
        "grok_replies": ["SKIP"],
    }
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")

        assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED

        assert isinstance(xai_user_content(server), str)
        assert xai_image_urls(server) == []
    finally:
        server.stop()


def test_mention_external_url_without_native_photo_is_not_image_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = {
        "mentions": [
            {
                "id": "100",
                "text": "Look at this https://example.com/story",
                "author_id": "200",
                "conversation_id": "100",
                "created_at": "2026-07-06T10:00:00Z",
            }
        ],
        "grok_replies": ["SKIP"],
    }
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")

        assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED

        content = xai_user_content(server)
        assert isinstance(content, str)
        assert "https://example.com/story" not in content
        assert xai_image_urls(server) == []
    finally:
        server.stop()


def test_mention_native_photo_context_caps_multiple_photos_in_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = {
        "mentions": [
            {
                "id": "100",
                "text": "Several images attached.",
                "author_id": "200",
                "conversation_id": "100",
                "created_at": "2026-07-06T10:00:00Z",
                "attachments": {"media_keys": ["3_a", "3_b", "3_c"]},
            }
        ],
        "mentions_extra": {
            "includes": {
                "media": [
                    {"media_key": "3_a", "type": "photo", "url": "https://pbs.twimg.com/media/a.jpg"},
                    {"media_key": "3_b", "type": "photo", "url": "https://pbs.twimg.com/media/b.jpg"},
                    {"media_key": "3_c", "type": "photo", "url": "https://pbs.twimg.com/media/c.jpg"},
                ]
            }
        },
        "grok_replies": ["SKIP"],
    }
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")

        assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED

        assert xai_image_urls(server) == [
            "https://pbs.twimg.com/media/a.jpg",
            "https://pbs.twimg.com/media/b.jpg",
        ]
    finally:
        server.stop()


def test_native_photo_unavailable_adds_incomplete_context_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = {
        "mentions": [
            {
                "id": "100",
                "text": "This only makes sense with the image. https://t.co/example",
                "author_id": "200",
                "conversation_id": "100",
                "created_at": "2026-07-06T10:00:00Z",
                "attachments": {"media_keys": ["3_100"]},
            }
        ],
        "mentions_extra": {
            "includes": {
                "media": [
                    {
                        "media_key": "3_100",
                        "type": "photo",
                    }
                ]
            }
        },
        "grok_replies": ["SKIP"],
    }
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")

        assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED

        content = xai_user_content(server)
        assert isinstance(content, str)
        assert "could not be made available" in content
        assert "Do not invent image contents" in content
        assert "https://t.co/example" not in content
    finally:
        server.stop()


def test_multimodal_xai_rejection_retries_with_incomplete_media_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = {
        "mentions": [
            {
                "id": "100",
                "text": "This depends on the image. https://t.co/example",
                "author_id": "200",
                "conversation_id": "100",
                "created_at": "2026-07-06T10:00:00Z",
                "attachments": {"media_keys": ["3_100"]},
            }
        ],
        "mentions_extra": {
            "includes": {
                "media": [
                    {
                        "media_key": "3_100",
                        "type": "photo",
                        "url": "https://pbs.twimg.com/media/native-photo.jpg",
                    }
                ]
            }
        },
        "xai_responses": [
            {"status": 400, "body": {"error": "image input rejected"}},
            {
                "status": 200,
                "body": {
                    "choices": [{"message": {"content": "SKIP"}}],
                    "usage": {"total_tokens": 12},
                },
            },
        ],
    }
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")

        assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED

        xai_posts = [request for request in server.requests if request["path"] == "/v1/chat/completions"]
        assert len(xai_posts) == 2
        assert isinstance(xai_posts[0]["body"]["messages"][1]["content"], list)
        retry_content = xai_posts[1]["body"]["messages"][1]["content"]
        assert isinstance(retry_content, str)
        assert "could not be made available" in retry_content
        assert len(server.xai_requests) == 1
    finally:
        server.stop()


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (400, {"error": "invalid request body"}),
        (403, {"error": "forbidden"}),
        (415, {"error": "unsupported content type application/json"}),
        (422, {"error": "unsupported input type"}),
        (422, {"error": "invalid provision"}),
        (422, {"error": "invalid revision"}),
        (422, {"error": "invalid supervision setting"}),
        (
            400,
            {
                "error": {"message": "invalid request body"},
                "request_fragment": {"type": "image_url"},
            },
        ),
        (401, {"error": "configured 401 failure"}),
        (429, {"error": "configured 429 failure"}),
        (503, {"error": "configured 503 failure"}),
    ],
)
def test_xai_multimodal_rejection_classifier_rejects_unrelated_errors(
    status_code: int,
    body: dict,
) -> None:
    assert bot.xai_error_is_multimodal_input_rejection(fake_xai_response(status_code, body)) is False


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (400, {"error": "unsupported image_url in multimodal input"}),
        (415, {"error": "image content type is unsupported"}),
        (422, {"error": "invalid multimodal image input"}),
        (422, {"error": "invalid vision input"}),
        (
            400,
            {
                "error": {"message": "unsupported image_url in multimodal input"},
                "request_fragment": {"type": "image_url"},
            },
        ),
    ],
)
def test_xai_multimodal_rejection_classifier_accepts_media_specific_errors(
    status_code: int,
    body: dict,
) -> None:
    assert bot.xai_error_is_multimodal_input_rejection(fake_xai_response(status_code, body)) is True


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (400, {"error": "invalid request body"}),
        (403, {"error": "forbidden"}),
        (415, {"error": "unsupported content type application/json"}),
        (422, {"error": "unsupported input type"}),
        (
            400,
            {
                "error": {"message": "invalid request body"},
                "request_fragment": {"type": "image_url"},
            },
        ),
        (401, {"error": "configured 401 failure"}),
        (429, {"error": "configured 429 failure"}),
        (503, {"error": "configured 503 failure"}),
    ],
)
def test_non_image_xai_http_errors_do_not_retry_as_text_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    body: dict,
) -> None:
    status, xai_posts, successful_xai_requests, _state = run_native_photo_mention_with_xai_responses(
        tmp_path,
        monkeypatch,
        [
            {"status": status_code, "body": body},
            {
                "status": 200,
                "body": {
                    "choices": [{"message": {"content": "SKIP"}}],
                    "usage": {"total_tokens": 12},
                },
            },
        ],
    )

    assert status == bot.NORMAL_CHECK_STATUS_API_ERROR
    assert len(xai_posts) == 1
    assert isinstance(xai_posts[0]["body"]["messages"][1]["content"], list)
    assert successful_xai_requests == []


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (400, {"error": "unsupported image_url in multimodal input"}),
        (415, {"error": "image content type is unsupported"}),
        (422, {"error": "invalid multimodal image input"}),
    ],
)
def test_image_xai_http_rejection_retries_once_as_text_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    body: dict,
) -> None:
    status, xai_posts, successful_xai_requests, _state = run_native_photo_mention_with_xai_responses(
        tmp_path,
        monkeypatch,
        [
            {"status": status_code, "body": body},
            {
                "status": 200,
                "body": {
                    "choices": [{"message": {"content": "SKIP"}}],
                    "usage": {"total_tokens": 12},
                },
            },
            {
                "status": 200,
                "body": {
                    "choices": [{"message": {"content": "This third response must not be used."}}],
                    "usage": {"total_tokens": 99},
                },
            },
        ],
    )

    assert status == bot.NORMAL_CHECK_STATUS_CHECKED
    assert len(xai_posts) == 2
    assert isinstance(xai_posts[0]["body"]["messages"][1]["content"], list)
    fallback_content = xai_posts[1]["body"]["messages"][1]["content"]
    assert isinstance(fallback_content, str)
    assert "could not be made available" in fallback_content
    assert "Do not invent image contents" in fallback_content
    assert len(successful_xai_requests) == 1


def test_quote_tweet_native_photo_context_reaches_xai(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    scenario["quote_tweets"]["900"]["data"][0]["attachments"] = {"media_keys": ["3_910"]}
    scenario["quote_tweets"]["900"]["includes"]["media"] = [
        {
            "media_key": "3_910",
            "type": "photo",
            "url": "https://pbs.twimg.com/media/quote-photo.jpg",
        }
    ]
    scenario["grok_replies"] = ["SKIP"]
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_QUOTE_REPLIES_PER_DAY", 10)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "QUOTE_REPLY_DELAY_SECONDS", 0)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        state = bot.default_state()
        state["recent_own_post_ids"] = ["900"]
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")
        state["daily_quote_reply_date"] = state["daily_reply_date"]

        assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED

        assert xai_image_urls(server) == ["https://pbs.twimg.com/media/quote-photo.jpg"]
    finally:
        server.stop()


def test_hot_post_reply_native_photo_context_reaches_xai(
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
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
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

        assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED

        assert xai_image_urls(server) == ["https://pbs.twimg.com/media/hot-photo.jpg"]
    finally:
        server.stop()


def test_confirmed_reply_receipt_reconciliation_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

    state = bot.default_state()
    state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")
    state["last_seen_mention_id"] = "99"
    receipt = {
        "schema_version": 1,
        "target_id": "100",
        "reply_post_id": "900000",
        "author_id": "200",
        "reply_epoch": fixed_epoch,
        "daily_reply_date": state["daily_reply_date"],
        "candidate_source": "mention",
        "conversation_id": "100",
        "reply_text": "A reply.",
    }

    bot.write_confirmed_reply_receipt(receipt)
    assert bot.reconcile_confirmed_reply_receipt(state) is True
    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()

    bot.write_confirmed_reply_receipt(receipt)
    assert bot.reconcile_confirmed_reply_receipt(state) is True

    assert state["daily_reply_count"] == 1
    assert state["daily_replied_author_counts"] == {"200": 1}
    assert state["replied_to_ids"].count("100") == 1
    assert state["own_auto_reply_ids"].count("900000") == 1
    assert state["last_seen_mention_id"] == "100"


def test_confirmed_reply_receipt_preserves_strategy_metadata_after_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
    state = bot.default_state()
    state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")
    metadata = {
        "mode": "historical_context", "humour_tone": "dry",
        "evidence_confidence": "medium", "retrieved_quote_ids": ["a" * 64],
        "evidence_summary": "A grounded summary.", "factual_claim_made": True,
        "grounded": True, "reply_text": "A grounded reply.", "no_reply_reason": "",
    }
    receipt = {
        "schema_version": 1, "target_id": "100", "reply_post_id": "900000",
        "author_id": "200", "reply_epoch": fixed_epoch,
        "daily_reply_date": state["daily_reply_date"], "candidate_source": "mention",
        "conversation_id": "100", "reply_text": "A grounded reply.",
        "strategy_metadata": metadata,
    }

    bot.write_confirmed_reply_receipt(receipt)
    assert bot.reconcile_confirmed_reply_receipt(state) is True
    bot.write_confirmed_reply_receipt(receipt)
    assert bot.reconcile_confirmed_reply_receipt(state) is True

    assert state["reply_strategy_history"] == [{
        "target_id": "100", "reply_post_id": "900000", "candidate_source": "mention",
        "reply_epoch": fixed_epoch, **metadata,
    }]


def test_confirmed_reply_receipt_rejects_malformed_strategy_strings() -> None:
    receipt = {
        "schema_version": 1, "target_id": "100", "reply_post_id": "900000",
        "author_id": "200", "reply_epoch": 2_000_000_000,
        "daily_reply_date": "2033-05-18", "candidate_source": "mention",
        "conversation_id": "100", "reply_text": "A grounded reply.",
        "strategy_metadata": {
            "mode": "historical_context", "humour_tone": "dry",
            "evidence_confidence": "medium", "retrieved_quote_ids": ["a" * 64],
            "evidence_summary": {"not": "a string"}, "factual_claim_made": True,
            "grounded": True, "reply_text": "A grounded reply.", "no_reply_reason": [],
        },
    }
    assert bot.confirmed_reply_receipt_is_semantically_valid(receipt) is False

    receipt["strategy_metadata"] = {
        **receipt["strategy_metadata"],
        "mode": [],
        "evidence_summary": "A summary.",
        "no_reply_reason": "",
    }
    assert bot.confirmed_reply_receipt_is_semantically_valid(receipt) is False


def test_confirmed_reply_receipt_rejects_contradictory_strategy_metadata() -> None:
    receipt = {
        "schema_version": 1, "target_id": "100", "reply_post_id": "900000",
        "author_id": "200", "reply_epoch": 2_000_000_000,
        "daily_reply_date": "2033-05-18", "candidate_source": "mention",
        "conversation_id": "100", "reply_text": "An alleged correction.",
        "strategy_metadata": {
            "mode": "historical_correction", "humour_tone": "dry",
            "evidence_confidence": "high", "retrieved_quote_ids": [],
            "evidence_summary": "", "factual_claim_made": False,
            "grounded": False, "reply_text": "An alleged correction.", "no_reply_reason": "",
        },
    }
    assert bot.confirmed_reply_receipt_is_semantically_valid(receipt) is False


def test_pending_strategy_reply_survives_state_round_trip_and_is_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    state = bot.default_state()
    metadata = {
        "mode": "wry_reply", "humour_tone": "wry", "evidence_confidence": "none",
        "retrieved_quote_ids": [], "evidence_summary": "", "factual_claim_made": False,
        "grounded": False, "reply_text": "The first draft remains the first draft.",
        "no_reply_reason": "",
    }
    reply = bot.ReplyDecision("The first draft remains the first draft.", metadata) if hasattr(bot, "ReplyDecision") else None
    if reply is None:
        from reply_strategy import ReplyDecision
        reply = ReplyDecision("The first draft remains the first draft.", metadata)
    bot.store_pending_strategy_reply(state, "100", "mention", reply)
    bot.save_state(state, durable=True)
    loaded = bot.load_state()
    reused = bot.pending_strategy_reply(loaded, "100", "mention")
    assert reused == reply
    assert reused.strategy_metadata == metadata


def test_confirmed_reply_reconciliation_clears_pending_strategy_draft() -> None:
    state = bot.default_state()
    metadata = {
        "mode": "wry_reply", "humour_tone": "wry", "evidence_confidence": "none",
        "retrieved_quote_ids": [], "evidence_summary": "", "factual_claim_made": False,
        "grounded": False, "reply_text": "A stable draft.", "no_reply_reason": "",
    }
    from reply_strategy import ReplyDecision
    bot.store_pending_strategy_reply(state, "100", "mention", ReplyDecision("A stable draft.", metadata))
    receipt = {
        "schema_version": 1, "target_id": "100", "reply_post_id": "900000",
        "author_id": "200", "reply_epoch": 2_000_000_000,
        "daily_reply_date": "2033-05-18", "candidate_source": "mention",
        "conversation_id": "100", "reply_text": "A stable draft.",
        "strategy_metadata": metadata,
    }
    bot.apply_confirmed_reply_receipt(state, receipt)
    assert bot.pending_strategy_reply(state, "100", "mention") is None


def test_malformed_pending_strategy_draft_is_not_reused() -> None:
    state = {
        "pending_reply_drafts": {
            "mention:100": {
                "target_id": "different", "candidate_source": "mention",
                "reply_text": "Unsafe stale draft.",
                "strategy_metadata": {"mode": "wry_reply", "reply_text": "Unsafe stale draft."},
            }
        }
    }
    assert bot.pending_strategy_reply(state, "100", "mention") is None


def test_pending_strategy_drafts_are_bounded() -> None:
    from reply_strategy import ReplyDecision
    state = bot.default_state()
    metadata = {
        "mode": "wry_reply", "humour_tone": "wry", "evidence_confidence": "none",
        "retrieved_quote_ids": [], "evidence_summary": "", "factual_claim_made": False,
        "grounded": False, "reply_text": "Stable draft.", "no_reply_reason": "",
    }
    reply = ReplyDecision("Stable draft.", metadata)
    for target in range(101, 202):
        bot.store_pending_strategy_reply(state, str(target), "mention", reply)
    assert len(state["pending_reply_drafts"]) == 100
    assert "mention:101" not in state["pending_reply_drafts"]
    assert "mention:201" in state["pending_reply_drafts"]


def test_confirmed_quote_tweet_reply_receipt_reconciliation_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

    state = bot.default_state()
    state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")
    state["daily_quote_reply_date"] = state["daily_reply_date"]
    receipt = {
        "schema_version": 1,
        "target_id": "910",
        "reply_post_id": "900000",
        "author_id": "310",
        "reply_epoch": fixed_epoch,
        "daily_reply_date": state["daily_reply_date"],
        "daily_quote_reply_date": state["daily_quote_reply_date"],
        "candidate_source": "quote_tweet",
        "conversation_id": "910",
        "reply_text": "A reply.",
        "original_post_id": "900",
    }

    bot.write_confirmed_reply_receipt(receipt)
    assert bot.reconcile_confirmed_reply_receipt(state) is True
    bot.write_confirmed_reply_receipt(receipt)
    assert bot.reconcile_confirmed_reply_receipt(state) is True

    assert state["daily_reply_count"] == 1
    assert state["daily_quote_reply_count"] == 1
    assert state["daily_replied_author_counts"] == {"310": 1}
    assert state["replied_to_quote_post_ids"].count("910") == 1
    assert state["seen_quote_post_ids"].count("910") == 1
    assert state["own_auto_reply_ids"].count("900000") == 1


def test_confirmed_reply_receipt_persistence_failure_keeps_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
    receipt = {
        "schema_version": 1,
        "target_id": "100",
        "reply_post_id": "900000",
        "author_id": "200",
        "reply_epoch": fixed_epoch,
        "daily_reply_date": datetime.fromtimestamp(fixed_epoch).strftime("%Y-%m-%d"),
        "candidate_source": "mention",
        "conversation_id": "100",
        "reply_text": "A reply.",
    }
    state = bot.default_state()
    state["daily_reply_date"] = receipt["daily_reply_date"]

    bot.write_confirmed_reply_receipt(receipt)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: (_ for _ in ()).throw(OSError("state failed")))

    with pytest.raises(bot.ConfirmedReplyLocalPersistenceError):
        bot.reconcile_confirmed_reply_receipt(state)

    assert bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    assert "100" in state["replied_to_ids"]


def test_confirmed_reply_normal_success_uses_durable_state_before_receipt_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(SCENARIOS / "normal_mention_reply.json")
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
        monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        original_save_state = bot.save_state
        save_calls: list[bool] = []
        receipt_remove_seen = False

        def tracking_save_state(state: dict, **kwargs: object) -> None:
            save_calls.append(bool(kwargs.get("durable", False)))
            original_save_state(state, **kwargs)

        def tracking_remove_receipt(receipt: dict | None = None) -> None:
            nonlocal receipt_remove_seen
            assert save_calls and save_calls[-1] is True
            receipt_remove_seen = True
            bot.CONFIRMED_REPLY_RECEIPT_FILE.unlink()

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")
        monkeypatch.setattr(bot, "save_state", tracking_save_state)
        monkeypatch.setattr(bot, "remove_confirmed_reply_receipt", tracking_remove_receipt)

        assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_POSTED
        assert receipt_remove_seen is True
    finally:
        server.stop()


def test_confirmed_reply_latest_backup_recovers_suppression_after_primary_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(SCENARIOS / "normal_mention_reply.json")
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        state_file = tmp_path / "bot_state.json"
        monkeypatch.setattr(bot, "STATE_FILE", state_file)
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
        monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")

        assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_POSTED
        assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
        latest_backup = json.loads((state_file.with_name("bot_state.json.bak1")).read_text(encoding="utf-8"))
        assert "100" in latest_backup["replied_to_ids"]

        state_file.write_text("{bad json", encoding="utf-8")
        recovered = bot.load_state()

        assert "100" in recovered["replied_to_ids"]
        assert recovered["last_seen_mention_id"] == "100"
        assert recovered["own_auto_reply_ids"] == ["900000"]
        assert recovered["daily_reply_count"] == 1
    finally:
        server.stop()


def test_malformed_confirmed_reply_receipt_blocks_mention_replies(tmp_path: Path) -> None:
    bot.CONFIRMED_REPLY_RECEIPT_FILE.write_text("{bad json", encoding="utf-8")
    state = bot.default_state()

    with pytest.raises(bot.InvalidConfirmedReplyReceipt):
        bot.reconcile_confirmed_reply_receipt(state)

    assert bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()


def test_confirmed_quote_tweet_reply_save_failure_replays_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    scenario["grok_replies"] = [
        "A point is useful only when it survives contact with reality. This one rather does.",
        "A point is useful only when it survives contact with reality. This one rather does.",
    ]
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        state_file = tmp_path / "bot_state.json"
        monkeypatch.setattr(bot, "STATE_FILE", state_file)
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
        monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
        monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_QUOTE_REPLIES_PER_DAY", 10)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "QUOTE_REPLY_DELAY_SECONDS", 0)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        original_save_state = bot.save_state
        initial_state = bot.default_state()
        initial_state.update(
            {
                "recent_own_post_ids": ["900"],
                "daily_reply_date": bot.current_datetime().strftime("%Y-%m-%d"),
                "daily_quote_reply_date": bot.current_datetime().strftime("%Y-%m-%d"),
                "daily_reply_count": 0,
                "daily_quote_reply_count": 0,
                "last_reply_epoch": 0,
                "replied_to_quote_post_ids": [],
                "seen_quote_post_ids": [],
                "daily_replied_author_ids": [],
                "daily_replied_author_counts": {},
                "own_auto_reply_ids": [],
                "tweet_cache": {},
            }
        )
        original_save_state(initial_state)

        def fail_first_post_success_save(state: dict, **kwargs: object) -> None:
            if server.posts:
                raise OSError("injected quote post-success save failure")
            original_save_state(state, **kwargs)

        monkeypatch.setattr(bot, "save_state", fail_first_post_success_save)
        first_state = bot.load_state()

        with pytest.raises(OSError, match="injected quote post-success save failure"):
            bot.maybe_reply_to_quote_tweets(first_state)

        assert len(server.posts) == 1
        first_reply_id = "900000"
        assert server.posts[0]["reply"]["in_reply_to_tweet_id"] == "910"

        durable_after_failed_save = json.loads(state_file.read_text(encoding="utf-8"))
        assert "910" not in durable_after_failed_save.get("replied_to_quote_post_ids", [])
        assert "910" not in durable_after_failed_save.get("seen_quote_post_ids", [])
        assert durable_after_failed_save.get("daily_reply_count") == 0
        assert durable_after_failed_save.get("daily_quote_reply_count") == 0
        assert durable_after_failed_save.get("last_reply_epoch") == 0
        assert durable_after_failed_save.get("daily_replied_author_counts", {}) == {}
        assert first_reply_id not in durable_after_failed_save.get("own_auto_reply_ids", [])

        monkeypatch.setattr(bot, "save_state", original_save_state)
        restarted_state = bot.load_state()
        second_status = bot.maybe_reply_to_quote_tweets(restarted_state)

        assert second_status == bot.QUOTE_CHECK_STATUS_CHECKED
        assert [post["reply"]["in_reply_to_tweet_id"] for post in server.posts] == ["910"]
    finally:
        server.stop()


def test_quote_tweet_receipt_reconciled_by_mention_lane_counts_quote_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    scenario["grok_replies"] = [
        "A point is useful only when it survives contact with reality. This one rather does.",
    ]
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        reply_date = datetime.fromtimestamp(fixed_epoch).strftime("%Y-%m-%d")
        prior_date = "2033-05-17"
        state_file = tmp_path / "bot_state.json"
        monkeypatch.setattr(bot, "STATE_FILE", state_file)
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
        monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
        monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_QUOTE_REPLIES_PER_DAY", 10)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "QUOTE_REPLY_DELAY_SECONDS", 0)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        original_save_state = bot.save_state
        initial_state = bot.default_state()
        initial_state.update(
            {
                "recent_own_post_ids": ["900"],
                "daily_reply_date": reply_date,
                "daily_quote_reply_date": prior_date,
                "daily_reply_count": 0,
                "daily_quote_reply_count": 0,
                "last_reply_epoch": 0,
                "replied_to_ids": [],
                "replied_to_quote_post_ids": [],
                "seen_quote_post_ids": [],
                "daily_replied_author_ids": [],
                "daily_replied_author_counts": {},
                "own_auto_reply_ids": [],
                "tweet_cache": {},
            }
        )
        original_save_state(initial_state)

        def fail_first_post_success_save(state: dict, **kwargs: object) -> None:
            if server.posts:
                raise OSError("injected quote post-success save failure")
            original_save_state(state, **kwargs)

        monkeypatch.setattr(bot, "save_state", fail_first_post_success_save)
        first_state = bot.load_state()

        with pytest.raises(OSError, match="injected quote post-success save failure"):
            bot.maybe_reply_to_quote_tweets(first_state)

        assert [post["reply"]["in_reply_to_tweet_id"] for post in server.posts] == ["910"]
        durable_after_failed_save = json.loads(state_file.read_text(encoding="utf-8"))
        assert durable_after_failed_save["daily_quote_reply_date"] == reply_date
        assert durable_after_failed_save["daily_quote_reply_count"] == 0
        assert bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()

        monkeypatch.setattr(bot, "save_state", original_save_state)
        restarted_state = bot.load_state()

        assert bot.maybe_reply_to_mentions(restarted_state) == bot.NORMAL_CHECK_STATUS_CHECKED
        assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
        assert restarted_state["daily_quote_reply_date"] == reply_date
        assert restarted_state["daily_quote_reply_count"] == 1

        bot.reset_daily_quote_reply_count_if_needed(restarted_state)
        assert restarted_state["daily_quote_reply_date"] == reply_date
        assert restarted_state["daily_quote_reply_count"] == 1
        assert [post["reply"]["in_reply_to_tweet_id"] for post in server.posts] == ["910"]
    finally:
        server.stop()


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
        "cached_epoch": fixed_epoch,
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


def test_quote_tweet_missing_created_at_is_not_old_enough() -> None:
    assert bot.quote_tweet_is_old_enough({"id": "123"}) is False
    assert bot.quote_tweet_is_old_enough({"id": "123", "created_at": "not a date"}) is False


@pytest.mark.parametrize(
    "state",
    [
        {
            "next_meme_post_epoch": 1_800_010_000,
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_schedule_mode": "delayed_recent_quote",
            "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
            "meme_anchor_quote_post_epoch": 1_800_000_000,
        },
        {
            "next_meme_post_epoch": 1_800_010_000,
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "next_meme_schedule_date": bot.epoch_date_str(1_800_000_000),
            "meme_anchor_quote_post_epoch": 0,
        },
        {
            "next_meme_post_epoch": 1_800_000_000,
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "next_meme_schedule_date": bot.epoch_date_str(1_800_000_000),
            "meme_anchor_quote_post_epoch": 1_800_000_000,
        },
        {
            "next_meme_post_epoch": 1_800_010_000,
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": "2000-01-01",
            "meme_anchor_quote_post_epoch": 0,
        },
    ],
)
def test_normalise_state_rejects_inconsistent_meme_schedule(state: dict, tmp_path: Path) -> None:
    assert bot.normalise_state_candidate(state, path=tmp_path / "bot_state.json") is None


def test_old_meme_schedule_version_survives_load_until_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "bot_state.json"
    old_state = {
        "meme_schedule_version": 1,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    bot.atomic_write_json(state_file, old_state)

    loaded = bot.load_state()

    assert loaded["meme_schedule_version"] == 1
    assert loaded["next_meme_post_epoch"] == 1_800_010_000


def test_ensure_meme_schedule_initialized_migrates_old_version(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {
        "meme_schedule_version": 1,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    saved: list[dict] = []
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: saved.append(json.loads(json.dumps(state))))

    bot.ensure_meme_schedule_initialized(state)

    assert state["meme_schedule_version"] == bot.MEME_SCHEDULE_VERSION
    assert state["next_meme_schedule_mode"] == "fallback_migrated"
    assert state["meme_anchor_quote_post_epoch"] == 0
    assert bot.validate_meme_schedule_state(state, path=Path("state.json"))
    assert saved


def test_future_meme_schedule_version_is_rejected(tmp_path: Path) -> None:
    state = {
        "meme_schedule_version": 999,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }

    assert bot.normalise_state_candidate(state, path=tmp_path / "bot_state.json") is None


def test_current_meme_schedule_version_valid_state_is_unchanged(tmp_path: Path) -> None:
    state = {
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }

    normalised = bot.normalise_state_candidate(state, path=tmp_path / "bot_state.json")

    assert normalised is not None
    for key, value in state.items():
        assert normalised[key] == value


def test_current_active_meme_schedule_must_be_receipt_compatible(tmp_path: Path) -> None:
    bad_state = {
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "next_meme_post_epoch": 100,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(100),
        "meme_anchor_quote_post_epoch": 0,
    }
    assert bot.normalise_state_candidate(bad_state, path=tmp_path / "bot_state.json") is None

    good_state = {
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    normalised = bot.normalise_state_candidate(good_state, path=tmp_path / "bot_state.json")
    assert normalised is not None

    receipt = valid_regular_receipt(
        next_meme_post_epoch=normalised["next_meme_post_epoch"],
        next_meme_schedule_mode=normalised["next_meme_schedule_mode"],
        next_meme_schedule_date=normalised["next_meme_schedule_date"],
        meme_anchor_quote_post_epoch=normalised["meme_anchor_quote_post_epoch"],
        meme_schedule_changed_by_quote=False,
    )
    assert bot.regular_post_receipt_is_semantically_valid(receipt)


def test_too_old_current_active_meme_schedule_recovers_from_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    bot.atomic_write_json(
        state_file,
        {
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_post_epoch": 100,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": bot.epoch_date_str(100),
            "meme_anchor_quote_post_epoch": 0,
        },
    )
    bot.atomic_write_json(
        tmp_path / "bot_state.json.bak1",
        {
            "replied_to_ids": ["from-bak1"],
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_post_epoch": 1_800_010_000,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
            "meme_anchor_quote_post_epoch": 0,
        },
    )

    recovered = bot.load_state()

    assert recovered["replied_to_ids"] == ["from-bak1"]
    assert recovered["next_meme_post_epoch"] == 1_800_010_000


def test_unresolved_meme_receipt_blocks_another_main_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.atomic_write_json(
        bot.MEME_POST_RECEIPT_FILE,
        {
            "schema_version": 1,
            "post_id": "970001",
            "meme_basename": "001_meme.png",
            "meme_post_epoch": 1_800_000_000,
            "next_meme_post_epoch": 1_800_086_400,
        },
    )

    bot.post_random_quote(lines_used, images_used, state)
    assert state["last_main_post_id"] == "950001"


def test_simultaneous_regular_and_meme_receipts_block_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.atomic_write_json(
        receipt_file,
        {
            "schema_version": 1,
            "post_id": "950001",
            "quote_hash": bot.quote_text_hash("Good quote."),
            "image_basename": "t01.jpg",
            "quote_post_epoch": 1_800_000_000,
            "next_quote_post_epoch": 1_800_007_200,
            "text": "Good quote.",
        },
    )
    bot.atomic_write_json(
        bot.MEME_POST_RECEIPT_FILE,
        {
            "schema_version": 1,
            "post_id": "970001",
            "meme_basename": "001_meme.png",
            "meme_post_epoch": 1_800_000_000,
            "next_meme_post_epoch": 1_800_086_400,
        },
    )

    with pytest.raises(bot.InvalidRegularPostReceipt):
        bot.reconcile_main_post_receipts(lines_used, images_used, state)


def test_post_next_meme_rejects_simultaneous_receipts_without_removing_either(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _lines_used, _images_used, _state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.atomic_write_json(
        receipt_file,
        {
            "schema_version": 1,
            "post_id": "950001",
            "quote_hash": bot.quote_text_hash("Good quote."),
            "image_basename": "t01.jpg",
            "quote_post_epoch": 1_800_000_000,
            "next_quote_post_epoch": 1_800_007_200,
            "text": "Good quote.",
        },
    )
    bot.atomic_write_json(
        bot.MEME_POST_RECEIPT_FILE,
        {
            "schema_version": 1,
            "post_id": "970001",
            "meme_basename": "001_meme.png",
            "meme_post_epoch": 1_800_000_000,
            "next_meme_post_epoch": 1_800_086_400,
        },
    )

    with pytest.raises(bot.InvalidMemePostReceipt):
        bot.post_next_meme({})

    assert receipt_file.exists()
    assert bot.MEME_POST_RECEIPT_FILE.exists()


def test_invalid_meme_receipt_blocks_main_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.MEME_POST_RECEIPT_FILE.write_text("{bad json", encoding="utf-8")

    with pytest.raises(bot.InvalidMemePostReceipt):
        bot.post_random_quote(lines_used, images_used, state)


@pytest.mark.parametrize("next_epoch", [1_800_000_000, 1_799_999_999])
def test_meme_receipt_requires_future_next_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    next_epoch: int,
) -> None:
    receipt_file = tmp_path / "meme_post_receipt.json"
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", receipt_file)
    bot.atomic_write_json(
        receipt_file,
        {
            "schema_version": 1,
            "post_id": "970001",
            "meme_basename": "001_meme.png",
            "meme_post_epoch": 1_800_000_000,
            "next_meme_post_epoch": next_epoch,
        },
    )

    with pytest.raises(bot.InvalidMemePostReceipt):
        bot.reconcile_meme_post_receipt({})


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

    def failing_open(path: str, flags: int) -> int:
        if Path(path) == tmp_path:
            raise OSError("directory fsync unavailable")
        return real_open(path, flags)

    monkeypatch.setattr(bot.os, "open", failing_open)

    with pytest.raises(OSError, match="directory fsync unavailable"):
        bot.atomic_write_json(target, {"ok": True}, durable=True)


def test_missing_per_image_analysis_is_excluded_with_valid_alternative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    missing_path = image_dir / "t01.jpg"
    valid_path = image_dir / "t02.jpg"
    missing_path.write_bytes(b"missing")
    valid_path.write_bytes(b"valid")
    analysis = image_analysis_for_paths([missing_path, valid_path])
    missing_hash = bot.file_sha256(missing_path)
    analysis["items"].pop(missing_hash)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)

    chosen = bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["anything"]}}, {})

    assert chosen["basename"] == "t02.jpg"


def test_image_hash_failure_excludes_image_with_valid_alternative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    bad_path = image_dir / "t01.jpg"
    good_path = image_dir / "t02.jpg"
    bad_path.write_bytes(b"bad")
    good_path.write_bytes(b"good")
    analysis = image_analysis_for_paths([bad_path, good_path])
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)
    real_hash = bot.current_image_sha256

    def maybe_fail(path: str) -> str:
        if path.endswith("t01.jpg"):
            raise OSError("read failed")
        return real_hash(path)

    monkeypatch.setattr(bot, "current_image_sha256", maybe_fail)

    chosen = bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["anything"]}}, {})

    assert chosen["basename"] == "t02.jpg"


def test_all_image_hash_failures_raise_global_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"bad")
    analysis = image_analysis_for_paths([image_path])
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)
    monkeypatch.setattr(bot, "current_image_sha256", lambda path: (_ for _ in ()).throw(OSError("read failed")))

    with pytest.raises(bot.GlobalImageUnavailable):
        bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["anything"]}}, {})


def test_invalid_per_image_analysis_type_is_excluded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"invalid")
    analysis = image_analysis_for_paths([image_path])
    image_hash = bot.file_sha256(image_path)
    analysis["items"][image_hash]["analysis"] = "bad"
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)

    with pytest.raises(bot.GlobalImageUnavailable):
        bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["anything"]}}, {})


def test_global_image_failure_is_not_retried_across_quotes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_quote(lines_used: set, *, excluded_quote_hashes: set[str] | None = None) -> dict:
        nonlocal calls
        calls += 1
        return {"line_no": calls - 1, "quote_hash": f"{calls:064x}", "text": f"Quote {calls}.", "analysis": {}}

    monkeypatch.setattr(bot, "reconcile_main_post_receipts", lambda *args, **kwargs: {"regular": False, "meme": False})
    monkeypatch.setattr(bot, "quote_used_history_has_legacy_indices", lambda used: False)
    monkeypatch.setattr(bot, "choose_unused_line_candidate", fake_quote)
    monkeypatch.setattr(bot, "choose_matched_unused_image", lambda *args, **kwargs: (_ for _ in ()).throw(bot.GlobalImageUnavailable("no corpus")))

    with pytest.raises(bot.GlobalImageUnavailable):
        bot.post_random_quote(set(), set(), {})

    assert calls == 1


def test_image_cycle_status_log_formats_without_argument_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"valid")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: image_analysis_for_paths([image_path]))

    bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["anything"]}}, {})

    assert "Image cycle status:" in caplog.text


def test_append_unique_capped_preserves_order_and_moves_existing_item_to_tail() -> None:
    assert bot.append_unique_capped(["a", "b", "c"], "b", 3) == ["a", "c", "b"]


def test_append_unique_capped_discards_oldest_items() -> None:
    assert bot.append_unique_capped(["a", "b", "c"], "d", 3) == ["b", "c", "d"]


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


def test_clean_generated_reply_never_exceeds_configured_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "MAX_REPLY_CHARS", 20)

    assert len(bot.clean_generated_reply("x" * 100)) <= 20
    assert len(bot.clean_generated_reply("several ordinary words " * 10)) <= 20


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_reply_prompt_handles_obvious_harmless_teasing_without_literal_correction(
    lane: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account_post = (
        "I know some of the images have been a bit odd of late. "
        "I'm working on it - bear with me..."
    )
    incoming = "Not her most memorable quote"
    if lane == "mention":
        context_text = (
            "Thread context, oldest to newest.\n\n"
            f"1. Parent post by this account, own_auto_reply=no:\n{account_post}\n\n"
            f"Incoming post/comment to answer:\n{incoming}"
        )
    else:
        context_text = bot.build_quote_tweet_context(
            {"text": account_post},
            {"author_id": "200", "text": incoming},
        )

    requests_seen: list[dict] = []

    def fake_post(*args: object, **kwargs: object) -> bot.requests.Response:
        requests_seen.append(kwargs["json"])
        return fake_xai_response(200, {"choices": [{"message": {"content": "SKIP"}}]})

    monkeypatch.setattr(bot.requests, "post", fake_post)

    assert bot.ask_grok_for_reply(context_text) is None
    assert len(requests_seen) == 1
    payload = requests_seen[0]
    assert payload["model"] == bot.XAI_MODEL
    assert [message["role"] for message in payload["messages"]] == ["system", "user"]
    prompt = payload["messages"][1]["content"]
    assert isinstance(prompt, str)
    assert account_post in prompt
    assert incoming in prompt
    assert "joke, tease, pun, sarcasm or light-hearted remark" in prompt
    assert "Do not respond literally to an obvious joke or tease" in prompt
    assert "do not pedantically correct a deliberately comic premise" in prompt
    assert "This wasn't meant as a quote at all." in prompt
    assert "History may overlook that one." in prompt
    assert "skip rather than force one" in prompt
    assert "Return exactly SKIP" in prompt


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


def test_schedule_next_quote_post_uses_configured_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    state: dict = {}
    monkeypatch.setattr(bot.random, "randint", lambda low, high: 123)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)

    bot.schedule_next_quote_post(state, from_epoch=1_000)

    assert state["next_quote_post_epoch"] == 1_123


def test_rate_limit_cooldown_uses_future_reset_with_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    state: dict = {}
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)

    bot.record_api_error(state, bot.ApiError("rate limited", service="x", status_code=429, reset_epoch=1_200), "x")

    assert state["api_cooldown_until_epoch"] == 1_260
    assert state["api_cooldown_reason"] == "x returned 429/rate limit"


@pytest.mark.parametrize("reset_epoch", [None, 900])
def test_rate_limit_cooldown_falls_back_for_missing_or_past_reset(
    monkeypatch: pytest.MonkeyPatch,
    reset_epoch: int | None,
) -> None:
    state: dict = {}
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)

    bot.record_api_error(state, bot.ApiError("rate limited", service="x", status_code=429, reset_epoch=reset_epoch), "x")

    assert state["api_cooldown_until_epoch"] == 1_000 + bot.COOLDOWN_AFTER_429_SECONDS


def test_clear_expired_api_cooldowns_clears_only_expired_values(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {
        "api_cooldown_until_epoch": 900,
        "api_cooldown_reason": "old",
        "quote_api_cooldown_until_epoch": 1_100,
        "quote_api_cooldown_reason": "still active",
    }
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_000)

    assert bot.clear_expired_api_cooldowns(state) is True
    assert state["api_cooldown_until_epoch"] == 0
    assert state["api_cooldown_reason"] == ""
    assert state["quote_api_cooldown_until_epoch"] == 1_100
    assert state["quote_api_cooldown_reason"] == "still active"


def test_sanitize_next_reply_lane_priority_normalizes_invalid_value() -> None:
    state = {"next_reply_lane_priority": "sideways"}

    assert bot.sanitize_next_reply_lane_priority(state) is True
    assert state["next_reply_lane_priority"] == "normal"


def test_sanitize_next_reply_lane_priority_accepts_valid_value() -> None:
    state = {"next_reply_lane_priority": "quote"}

    assert bot.sanitize_next_reply_lane_priority(state) is False
    assert state["next_reply_lane_priority"] == "quote"


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


def apply_local_config_for_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    data: dict[str, object],
    *,
    initial: dict[str, object] | None = None,
    expect_error: bool = False,
) -> dict[str, object]:
    config_file = tmp_path / "mrsMThatcher.local.json"
    config_file.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", config_file)
    for key, value in (initial or {}).items():
        monkeypatch.setattr(bot, key, value)
    before = {
        key: getattr(bot, key)
        for key in bot.LOCAL_CONFIG_ALLOWED_KEYS
        if hasattr(bot, key)
    }
    if expect_error:
        with pytest.raises(bot.LocalConfigError):
            bot.apply_local_config()
    else:
        bot.apply_local_config()
    return before


def test_local_config_interacting_invalid_overrides_are_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {"POST_SLEEP_MIN": 10000, "POST_SLEEP_MAX": 5000},
        initial={"POST_SLEEP_MIN": 7200, "POST_SLEEP_MAX": 9000},
        expect_error=True,
    )

    assert bot.POST_SLEEP_MIN == before["POST_SLEEP_MIN"] == 7200
    assert bot.POST_SLEEP_MAX == before["POST_SLEEP_MAX"] == 9000


def test_local_config_valid_multi_key_override_applies_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {
            "POST_SLEEP_MIN": 8000,
            "POST_SLEEP_MAX": 8200,
            "ENABLE_DAILY_MEME_POSTS": False,
            "MAX_MENTIONS_PER_CHECK": 10,
        },
        initial={
            "POST_SLEEP_MIN": 7200,
            "POST_SLEEP_MAX": 9000,
            "ENABLE_DAILY_MEME_POSTS": True,
            "MAX_MENTIONS_PER_CHECK": 5,
        },
    )

    assert bot.POST_SLEEP_MIN == 8000
    assert bot.POST_SLEEP_MAX == 8200
    assert bot.ENABLE_DAILY_MEME_POSTS is False
    assert bot.MAX_MENTIONS_PER_CHECK == 10


def test_local_config_coercion_failure_rejects_whole_transaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {"POST_SLEEP_MIN": 8000, "POST_SLEEP_MAX": 8200, "ENABLE_AUTO_REPLIES": "maybe"},
        initial={
            "POST_SLEEP_MIN": 7200,
            "POST_SLEEP_MAX": 9000,
            "ENABLE_AUTO_REPLIES": True,
        },
        expect_error=True,
    )

    assert bot.POST_SLEEP_MIN == before["POST_SLEEP_MIN"] == 7200
    assert bot.POST_SLEEP_MAX == before["POST_SLEEP_MAX"] == 9000
    assert bot.ENABLE_AUTO_REPLIES is before["ENABLE_AUTO_REPLIES"] is True


def test_local_config_mixed_valid_and_invalid_values_do_not_partially_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {"POST_SLEEP_MIN": 8000, "POST_SLEEP_MAX": 8200, "MAX_MENTIONS_PER_CHECK": 1},
        initial={
            "POST_SLEEP_MIN": 7200,
            "POST_SLEEP_MAX": 9000,
            "MAX_MENTIONS_PER_CHECK": 5,
        },
        expect_error=True,
    )

    assert bot.POST_SLEEP_MIN == before["POST_SLEEP_MIN"] == 7200
    assert bot.POST_SLEEP_MAX == before["POST_SLEEP_MAX"] == 9000
    assert bot.MAX_MENTIONS_PER_CHECK == before["MAX_MENTIONS_PER_CHECK"] == 5


def test_local_config_unsupported_key_cannot_override_arbitrary_globals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_func = bot.log_json_debug
    apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {"log_json_debug": None, "X_API_BASE_URL": "https://evil.invalid", "POST_SLEEP_MIN": 7300, "POST_SLEEP_MAX": 7400},
        initial={"POST_SLEEP_MIN": 7200, "POST_SLEEP_MAX": 9000},
    )

    assert bot.log_json_debug is original_func
    assert bot.POST_SLEEP_MIN == 7300
    assert bot.POST_SLEEP_MAX == 7400
    assert not hasattr(bot, "X_API_BASE_URL")


def test_local_config_existing_production_style_overrides_still_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {
            "ENABLE_AUTO_REPLIES": True,
            "MIN_SECONDS_BETWEEN_REPLIES": 1800,
            "MAX_AUTO_REPLIES_PER_DAY": 24,
            "MAX_QUOTE_REPLIES_PER_DAY": 12,
            "POST_SLEEP_MIN": 7200,
            "POST_SLEEP_MAX": 9000,
        },
        initial={
            "ENABLE_AUTO_REPLIES": False,
            "MIN_SECONDS_BETWEEN_REPLIES": 1,
            "MAX_AUTO_REPLIES_PER_DAY": 2,
            "MAX_QUOTE_REPLIES_PER_DAY": 1,
            "POST_SLEEP_MIN": 100,
            "POST_SLEEP_MAX": 200,
        },
    )

    assert bot.ENABLE_AUTO_REPLIES is True
    assert bot.MIN_SECONDS_BETWEEN_REPLIES == 1800
    assert bot.MAX_AUTO_REPLIES_PER_DAY == 24
    assert bot.MAX_QUOTE_REPLIES_PER_DAY == 12
    assert bot.POST_SLEEP_MIN == 7200
    assert bot.POST_SLEEP_MAX == 9000


def test_default_minimum_reply_spacing_is_30_minutes() -> None:
    assert bot.MIN_SECONDS_BETWEEN_REPLIES == 1800


def test_local_config_can_enable_generated_image_pool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    generated_dir = tmp_path / "generated"
    generated_analysis = tmp_path / "generated_image_analysis.json"
    apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {
            "ENABLE_GENERATED_IMAGE_POOL": True,
            "GENERATED_IMAGE_DIR": str(generated_dir),
            "GENERATED_IMAGE_GLOB": "*.png",
            "GENERATED_IMAGE_ANALYSIS_FILE": str(generated_analysis),
            "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST": 6,
            "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": 3,
        },
        initial={
            "ENABLE_GENERATED_IMAGE_POOL": False,
            "GENERATED_IMAGE_DIR": "",
            "GENERATED_IMAGE_GLOB": "*.jpg",
            "GENERATED_IMAGE_ANALYSIS_FILE": "",
            "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST": 4,
            "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": 2,
        },
    )

    assert bot.ENABLE_GENERATED_IMAGE_POOL is True
    assert bot.GENERATED_IMAGE_DIR == str(generated_dir)
    assert bot.GENERATED_IMAGE_GLOB == "*.png"
    assert bot.GENERATED_IMAGE_ANALYSIS_FILE == str(generated_analysis)
    assert bot.GENERATED_IMAGE_ORIGIN_QUOTE_BOOST == 6
    assert bot.GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN == 3


@pytest.mark.parametrize("bad_value", [-1, True, False, 2.0, 2.5, "2", "x"])
def test_local_config_rejects_invalid_generated_image_spacing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_value: object,
) -> None:
    before = apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {"GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": bad_value},
        initial={"GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": 2},
        expect_error=True,
    )

    assert bot.GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN == before["GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN"] == 2

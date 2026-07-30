from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

import mrsMThatcher2 as bot
import mrs_log_digest as digest


def install_paths(monkeypatch: pytest.MonkeyPatch, base: Path) -> None:
    monkeypatch.setattr(bot, "BASE_DIR", base)
    monkeypatch.setattr(bot, "STATE_FILE", base / "bot_state.json")
    monkeypatch.setattr(bot, "LINES_USED_FILE", base / "lines_used.json")
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", base / "images_used.json")
    monkeypatch.setattr(bot, "INSTALLATION_MARKER_FILE", base / ".mrsMThatcher.initialised.json")
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", base / "regular_post_receipt.json")
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", base / "meme_post_receipt.json")
    monkeypatch.setattr(bot, "CONFIRMED_REPLY_RECEIPT_FILE", base / "confirmed_reply_receipt.json")
    monkeypatch.setattr(bot, "AMBIGUOUS_POST_OUTCOME_FILE", base / "ambiguous_post_outcome.json")
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_HISTORY_FILE",
        base / "historical_context_reply_history.json",
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE",
        base / "historical_context_reply_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE",
        base / "historical_context_reply_outbox.json",
    )
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)


def test_explicit_initialisation_and_missing_file_matrix(tmp_path, monkeypatch):
    install_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    assert bot.initialise_installation() == 0
    for name in ("bot_state.json", "lines_used.json", "images_used.json", ".mrsMThatcher.initialised.json"):
        assert (tmp_path / name).is_file()
    bot.require_established_installation()
    with pytest.raises(RuntimeError, match="Refusing to initialise"):
        bot.initialise_installation()

    for missing in ("bot_state.json", "lines_used.json", "images_used.json"):
        path = tmp_path / missing
        content = path.read_bytes()
        path.unlink()
        removed_backups: list[tuple[Path, bytes]] = []
        if missing == "bot_state.json":
            for backup in tmp_path.glob("bot_state.json.bak*"):
                removed_backups.append((backup, backup.read_bytes()))
                backup.unlink()
        with pytest.raises(RuntimeError, match="Required durable"):
            bot.require_established_installation()
        path.write_bytes(content)
        for backup, backup_content in removed_backups:
            backup.write_bytes(backup_content)


def test_state_backup_is_an_existing_recovery_candidate(tmp_path, monkeypatch):
    install_paths(monkeypatch, tmp_path)
    (tmp_path / "lines_used.json").write_text("[]")
    (tmp_path / "images_used.json").write_text("[]")
    (tmp_path / "bot_state.json.bak1").write_text("{}")
    bot.require_established_installation()


@pytest.mark.parametrize(
    "value,valid",
    [
        (True, False), (False, False), (1, True), (0, True), (1.0, True),
        (1.5, False), ("1", False), ("2026-07-11 12:00", True),
        (None, False), (math.nan, False), (math.inf, False), (-1, False),
        (bot.MAX_REASONABLE_STATE_EPOCH + 1, False),
    ],
)
def test_control_timestamp_types(value, valid):
    if valid:
        assert type(bot.parse_control_time(value)) is int
    else:
        with pytest.raises(ValueError):
            bot.parse_control_time(value)


@pytest.mark.parametrize("value", [None, True, 123, 1.5, [], {}, "", " model "])
def test_string_config_requires_clean_json_string(value):
    with pytest.raises(ValueError):
        bot._coerce_local_config_value("XAI_MODEL", value, "grok-4.3")
    assert bot._coerce_local_config_value("XAI_MODEL", "grok-test", "grok-4.3") == "grok-test"


def test_control_cache_detects_atomic_replace_same_mtime(tmp_path, monkeypatch):
    path = tmp_path / "control.json"
    path.write_text(json.dumps({"disable_all": True}))
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    monkeypatch.setattr(bot, "_CONTROL_CACHE", {"signature": None, "data": {}, "has_valid": False, "failure_signature": None})
    assert bot.load_control()["disable_all"] is True
    old = path.stat()
    replacement = tmp_path / "replacement.json"
    replacement.write_text(json.dumps({"disable_all": False}))
    os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
    os.replace(replacement, path)
    assert bot.load_control()["disable_all"] is False


def test_explicit_canonical_log_expands_only_numeric_rotations(tmp_path):
    current = tmp_path / "mrsMThatcher.log"
    current.write_text("")
    for name in ("mrsMThatcher.log.1", "mrsMThatcher.log.3", "mrsMThatcher.log.selftest", "other.log.1"):
        (tmp_path / name).write_text("")
    paths = digest.resolve_explicit_logs([Path("mrsMThatcher.log"), current.with_name("mrsMThatcher.log.1")], tmp_path)
    assert [path.name for path in paths] == ["mrsMThatcher.log", "mrsMThatcher.log.1", "mrsMThatcher.log.3"]


def test_digest_execution_lock_is_nonblocking_and_released(tmp_path):
    lock = tmp_path / "digest.lock"
    with digest.digest_execution_lock(lock):
        with pytest.raises(RuntimeError, match="Another digest process"):
            with digest.digest_execution_lock(lock):
                pass
    with digest.digest_execution_lock(lock):
        pass


def test_digest_main_releases_lock_after_failure(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    log = project / "mrsMThatcher.log"
    log.write_text("")
    monkeypatch.setattr(digest, "run_digest", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("render failed")))
    args = ["--project-dir", str(project), "--state-file", "resume.json", str(log)]
    with pytest.raises(RuntimeError, match="render failed"):
        digest.main(args)
    lock = project / "resume.json.lock"
    with digest.digest_execution_lock(lock):
        pass


def test_no_state_stdout_run_does_not_take_digest_lock(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    log = project / "mrsMThatcher.log"
    log.write_text("")
    monkeypatch.setattr(digest, "run_digest", lambda *_a, **_k: 7)
    assert digest.main(["--project-dir", str(project), "--no-state", str(log)]) == 7
    assert not (project / ".mrs_log_digest_state.json.lock").exists()


def test_digest_schedule_defaults_match_production_source_defaults():
    expected = {
        "POST_SLEEP_MIN": bot.POST_SLEEP_MIN,
        "POST_SLEEP_MAX": bot.POST_SLEEP_MAX,
        "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": bot.GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN,
        "ENABLE_GENERATED_IMAGE_POOL": bot.ENABLE_GENERATED_IMAGE_POOL,
    }
    assert digest.RUNWAY_CONFIG_DEFAULTS == expected


def test_ambiguous_remote_post_creates_durable_blocker(tmp_path, monkeypatch):
    install_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(
        bot,
        "x_request",
        lambda *_a, **_k: (_ for _ in ()).throw(bot.AmbiguousRemotePostOutcome("timeout", service="x")),
    )
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.create_post("test")
    marker = json.loads((tmp_path / "ambiguous_post_outcome.json").read_text())
    assert marker["outcome"] == "ambiguous_remote_post"
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="Unreconciled ambiguous"):
        bot.block_if_ambiguous_remote_post()


def test_ambiguous_remote_post_blocks_process_when_marker_write_fails(tmp_path, monkeypatch):
    install_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    calls = 0

    def ambiguous_request(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise bot.AmbiguousRemotePostOutcome("timeout", service="x")

    monkeypatch.setattr(bot, "x_request", ambiguous_request)
    monkeypatch.setattr(
        bot,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("storage unavailable")),
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="timeout"):
        bot.create_post("first")
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="in-process remote-write safety latch"):
        bot.create_post("second")

    assert calls == 1


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
def test_x_server_error_on_post_creates_durable_ambiguity_barrier(
    status_code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_paths(monkeypatch, tmp_path)

    class Response:
        text = '{"detail":"upstream unavailable"}'
        headers: dict[str, str] = {}

        def __init__(self, status: int) -> None:
            self.status_code = status

    monkeypatch.setattr(bot.requests, "request", lambda *_args, **_kwargs: Response(status_code))

    with pytest.raises(bot.AmbiguousRemotePostOutcome) as caught:
        bot.create_post("test")

    assert caught.value.status_code == status_code
    marker = json.loads((tmp_path / "ambiguous_post_outcome.json").read_text(encoding="utf-8"))
    assert marker["outcome"] == "ambiguous_remote_post"
    assert bot.ambiguous_remote_post_is_blocking() is True


@pytest.mark.parametrize("status_code", [400, 403, 429])
def test_x_client_error_without_provider_contract_creates_ambiguity_barrier(
    status_code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_paths(monkeypatch, tmp_path)

    class Response:
        text = '{"detail":"request rejected"}'
        headers: dict[str, str] = {}

        def __init__(self, status: int) -> None:
            self.status_code = status

    monkeypatch.setattr(bot.requests, "request", lambda *_args, **_kwargs: Response(status_code))

    with pytest.raises(bot.AmbiguousRemotePostOutcome) as caught:
        bot.create_post("test")

    assert caught.value.status_code == status_code
    marker = json.loads(
        (tmp_path / "ambiguous_post_outcome.json").read_text(encoding="utf-8")
    )
    assert marker["outcome"] == "ambiguous_remote_post"
    assert bot.ambiguous_remote_post_is_blocking() is True


@pytest.mark.parametrize(
    "lane",
    [
        "regular_quote",
        "meme",
        "mention",
        "quote_tweet",
        "historical_context",
        "direct_create",
        "direct_media_upload",
    ],
)
@pytest.mark.parametrize(
    "barrier_kind",
    ["durable_marker", "confirmed_persistence_in_process"],
)
def test_existing_ambiguity_marker_blocks_each_lane_before_preparation(
    lane: str,
    barrier_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import historical_context_formatter

    install_paths(monkeypatch, tmp_path)
    if barrier_kind == "durable_marker":
        (tmp_path / "ambiguous_post_outcome.json").write_text("{}\n", encoding="utf-8")
    else:
        monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", True)
    calls: list[str] = []

    def prepared(name: str):
        calls.append(name)
        pytest.fail(f"{name} preparation must not run after an ambiguous post")

    if lane == "regular_quote":
        monkeypatch.setattr(bot, "reconcile_main_post_receipts", lambda *_args: prepared("receipt reconciliation"))
        invoke = lambda: bot.post_random_quote(set(), set(), bot.default_state())
    elif lane == "meme":
        monkeypatch.setattr(bot, "both_main_post_receipts_exist", lambda: prepared("meme receipt check"))
        invoke = lambda: bot.post_next_meme(bot.default_state())
    elif lane == "mention":
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "get_mentions", lambda *_args: prepared("mention fetch"))
        invoke = lambda: bot.maybe_reply_to_mentions(bot.default_state())
    elif lane == "quote_tweet":
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
        monkeypatch.setattr(bot, "build_quote_lookup_post_ids", lambda *_args: prepared("quote lookup"))
        invoke = lambda: bot.maybe_reply_to_quote_tweets(bot.default_state())
    elif lane == "historical_context":
        monkeypatch.setattr(bot, "historical_context_reply", {**bot.historical_context_reply, "enabled": True})
        monkeypatch.setattr(
            historical_context_formatter,
            "load_and_validate_corpus",
            lambda *_args: prepared("historical research load"),
        )
        invoke = lambda: bot.maybe_post_historical_context_reply(
            quote_hash="a" * 64,
            quote_text="Quote",
            parent_post_id="123",
        )
    elif lane == "direct_create":
        monkeypatch.setattr(bot, "x_request", lambda *_args, **_kwargs: prepared("X request"))
        invoke = lambda: bot.create_post("test")
    else:
        monkeypatch.setattr(bot, "upload_media_v2", lambda *_args, **_kwargs: prepared("media upload"))
        monkeypatch.setattr(bot, "upload_media_v1_1", lambda *_args, **_kwargs: prepared("media upload fallback"))
        invoke = lambda: bot.upload_media(str(tmp_path / "image.png"))

    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="Unreconciled"):
        invoke()

    assert calls == []


@pytest.mark.parametrize(
    ("body", "message"),
    [(b"not json", "non-JSON"), (b"[]", "JSON object")],
)
def test_success_status_malformed_body_is_ambiguous_only_for_writes(monkeypatch, body, message):
    response = bot.requests.Response()
    response.status_code = 200
    response._content = body
    monkeypatch.setattr(bot.requests, "request", lambda *args, **kwargs: response)

    with pytest.raises(bot.AmbiguousRemotePostOutcome, match=message):
        bot.x_request("POST", "/2/tweets", ambiguous_write=True, json={"text": "test"})
    with pytest.raises(bot.ApiError, match=message) as exc_info:
        bot.x_request("GET", "/2/users/me")
    monkeypatch.setattr(bot, "X_BEARER_TOKEN", "test-token")
    with pytest.raises(bot.ApiError, match=message) as bearer_exc_info:
        bot.x_bearer_request("GET", "/2/users/me")

    assert type(exc_info.value) is bot.ApiError
    assert type(bearer_exc_info.value) is bot.ApiError


@pytest.mark.parametrize(
    "page",
    [
        {"data": {}},
        {"data": ["not-an-object"]},
        {"includes": []},
        {"includes": {"users": {"id": "1"}}},
        {"includes": {"media": "bad"}},
        {"meta": []},
    ],
)
def test_paginated_get_rejects_malformed_page_sections(page):
    with pytest.raises(bot.ApiError, match="malformed paginated response"):
        bot.x_paginated_get(
            lambda _path, _params: page,
            "/2/test",
            {},
            max_pages=1,
            label="test",
        )


@pytest.mark.parametrize("data", [[], "not-an-object", 7])
def test_get_tweet_by_id_rejects_malformed_data(monkeypatch, data):
    monkeypatch.setattr(bot, "x_request", lambda *_args, **_kwargs: {"data": data})

    with pytest.raises(bot.ApiError, match="malformed tweet data"):
        bot.get_tweet_by_id("123")

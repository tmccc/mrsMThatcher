from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.fake_api_server import FakeApiServer, load_scenario


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "mrsMThatcher2.py"
DIGEST = ROOT / "mrs_log_digest.py"
SCENARIOS = ROOT / "tests" / "fixtures" / "scenarios"
PRODUCTION_BASE_DIR = Path("/disks/disk1/etc/mrsMThatcher")


def read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def prepare_base_dir(
    tmp_path: Path,
    *,
    local_config: dict | None = None,
    state: dict | None = None,
    watch_ids: list[str] | None = None,
    control: dict | None = None,
    meme: bool = False,
) -> Path:
    base_dir = tmp_path / "mrs-test-state"
    base_dir.mkdir(parents=True)
    (base_dir / "mrsMThatcher.txt").write_text("A test quote.\n", encoding="utf-8")
    (base_dir / "images").mkdir()
    (base_dir / "images" / "t01.jpg").write_bytes(b"fake image bytes")

    config = {
        "ENABLE_DAILY_MEME_POSTS": False,
        "MIN_SECONDS_BETWEEN_REPLIES": 0,
        "QUOTE_REPLY_DELAY_SECONDS": 0,
        "STATE_BACKUP_COUNT": 0,
        "MAX_MENTIONS_PER_CHECK": 10,
        "MAX_AUTO_REPLIES_PER_DAY": 24,
        "MAX_REPLIES_PER_AUTHOR_PER_DAY": 1,
    }
    if local_config:
        config.update(local_config)
    write_json(base_dir / "mrsMThatcher.local.json", config)

    if state is not None:
        write_json(base_dir / "bot_state.json", state)
    if watch_ids is not None:
        (base_dir / "extra_quote_watch_post_ids.txt").write_text("\n".join(watch_ids) + "\n", encoding="utf-8")
    if control is not None:
        write_json(base_dir / "mrsMThatcher.control.json", control)
    if meme:
        meme_dir = base_dir / "final_posting_queue_top90_as_is" / "images"
        meme_dir.mkdir(parents=True)
        (meme_dir / "001_test_meme.png").write_bytes(b"fake meme image bytes")
        write_json(base_dir / "final_posting_queue_top90_as_is" / "renamed_png_v3_top90_posting_queue.json", {"results": []})

    return base_dir


def run_bot_command(
    base_dir: Path,
    server: FakeApiServer,
    command: str = "--test-cycle",
    *,
    x_api_base_url: str | None = None,
    x_upload_base_url: str | None = None,
    xai_api_base_url: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "MRS_TEST_MODE": "1",
            "MRS_BASE_DIR": str(base_dir),
            "MRS_LOG_FILE": str(base_dir / "test.log"),
            "X_API_BASE_URL": x_api_base_url or server.url,
            "X_UPLOAD_BASE_URL": x_upload_base_url or server.url,
            "XAI_API_BASE_URL": xai_api_base_url or f"{server.url}/v1",
            "X_CONSUMER_KEY": "dummy",
            "X_CONSUMER_SECRET": "dummy",
            "X_ACCESS_TOKEN": "dummy",
            "X_ACCESS_SECRET": "dummy",
            "X_MY_USER_ID": "12345",
            "XAI_API_KEY": "dummy",
            "X_BEARER_TOKEN": "dummy",
            "LOG_LEVEL": "INFO",
        }
    )
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(BOT), command],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def run_bot_with_env(base_dir: Path, command: str = "--test-cycle", *, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "MRS_TEST_MODE": "1",
            "MRS_BASE_DIR": str(base_dir),
            "MRS_LOG_FILE": str(base_dir / "test.log"),
            "X_CONSUMER_KEY": "dummy",
            "X_CONSUMER_SECRET": "dummy",
            "X_ACCESS_TOKEN": "dummy",
            "X_ACCESS_SECRET": "dummy",
            "X_MY_USER_ID": "12345",
            "XAI_API_KEY": "dummy",
            "LOG_LEVEL": "INFO",
        }
    )
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(BOT), command],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def run_cycle(base_dir: Path, server: FakeApiServer) -> subprocess.CompletedProcess[str]:
    return run_bot_command(base_dir, server, "--test-cycle")


def run_digest(base_dir: Path, *, state_file: Path | None = None) -> subprocess.CompletedProcess[str]:
    args = [sys.executable, str(DIGEST), "--glob", "test.log"]
    if state_file is None:
        args.append("--no-state")
    else:
        args.extend(["--state-file", str(state_file)])
    args.append(str(base_dir / "test.log"))
    return subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def event_payloads(base_dir: Path) -> list[dict]:
    payloads = []
    log_path = base_dir / "test.log"
    if not log_path.exists():
        return payloads
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        marker = " EVENT "
        if marker not in line:
            continue
        text = line.split(marker, 1)[1]
        payloads.append(json.loads(text))
    return payloads


@pytest.fixture
def fake_server(request):
    scenario = load_scenario(SCENARIOS / request.param)
    server = FakeApiServer(scenario).start()
    try:
        yield server
    finally:
        server.stop()


@pytest.mark.parametrize("fake_server", ["normal_mention_reply.json"], indirect=True)
def test_normal_mention_reply(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path)
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    assert len(fake_server.posts) == 1
    assert fake_server.posts[0]["reply"]["in_reply_to_tweet_id"] == "100"
    state = read_json(base_dir / "bot_state.json")
    assert "100" in state["replied_to_ids"]
    assert state["last_seen_mention_id"] == "100"
    assert not (PRODUCTION_BASE_DIR / "bot_state.json.tmp").exists()


def test_quote_reply_flips_priority_to_normal(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "quote_tweet_reply.json")).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={"next_reply_lane_priority": "quote", "recent_own_post_ids": ["900"], "last_reply_epoch": 0},
            local_config={"ENABLE_HOT_POST_REPLY_CHECKS": False},
        )
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        assert state["next_reply_lane_priority"] == "normal"
        assert server.posts[0]["reply"]["in_reply_to_tweet_id"] == "910"
    finally:
        server.stop()


def test_normal_reply_flips_priority_to_quote(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(tmp_path, state={"next_reply_lane_priority": "normal", "last_reply_epoch": 0})
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        assert state["next_reply_lane_priority"] == "quote"
    finally:
        server.stop()


def test_preferred_empty_quote_lane_allows_normal_lane_to_use_slot(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(tmp_path, state={"next_reply_lane_priority": "quote", "last_reply_epoch": 0})
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        assert len(server.posts) == 1
        assert server.posts[0]["reply"]["in_reply_to_tweet_id"] == "100"
        state = read_json(base_dir / "bot_state.json")
        assert state["next_reply_lane_priority"] == "quote"
    finally:
        server.stop()


def test_regular_quote_and_meme_posts_do_not_alter_reply_priority(tmp_path: Path) -> None:
    server = FakeApiServer({"next_post_id": 950000}).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            meme=True,
            state={"next_reply_lane_priority": "quote"},
            local_config={"ENABLE_DAILY_MEME_POSTS": True, "MEME_POST_TEXT": "meme"},
        )
        quote_result = run_bot_command(base_dir, server, "--test-post-quote")
        assert quote_result.returncode == 0, quote_result.stderr + quote_result.stdout
        assert read_json(base_dir / "bot_state.json")["next_reply_lane_priority"] == "quote"

        meme_result = run_bot_command(base_dir, server, "--test-post-meme")
        assert meme_result.returncode == 0, meme_result.stderr + meme_result.stdout
        assert read_json(base_dir / "bot_state.json")["next_reply_lane_priority"] == "quote"
    finally:
        server.stop()


def test_hot_post_watermark_edges_and_full_rescan(tmp_path: Path) -> None:
    scenario = {
        "search_recent": [
            {
                "id": "300",
                "text": "A usable hot reply",
                "author_id": "400",
                "conversation_id": "700",
                "referenced_tweets": [{"type": "replied_to", "id": "700"}],
                "created_at": "2026-06-30T12:00:00Z",
            }
        ],
        "grok_replies": ["A sensible answer to the hot post."],
    }
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(tmp_path, watch_ids=["700"])

        first = run_cycle(base_dir, server)
        assert first.returncode == 0, first.stderr + first.stdout
        state = read_json(base_dir / "bot_state.json")
        assert state["hot_post_reply_since_ids"] == {}
        assert "300" in state["replied_to_ids"]

        scenario["search_recent"] = [
            {
                "id": "301",
                "text": "Not actually a reply",
                "author_id": "401",
                "conversation_id": "700",
                "created_at": "2026-06-30T12:01:00Z",
            }
        ]
        second = run_cycle(base_dir, server)
        assert second.returncode == 0, second.stderr + second.stdout
        state = read_json(base_dir / "bot_state.json")
        assert state["hot_post_reply_since_ids"]["700"] == "301"

        scenario["search_recent"] = [
            {
                "id": "302",
                "text": "A new reply after watermark",
                "author_id": "402",
                "conversation_id": "700",
                "referenced_tweets": [{"type": "replied_to", "id": "700"}],
                "created_at": "2026-06-30T12:02:00Z",
            }
        ]
        scenario["grok_replies"] = ["Reply after watermark."]
        third = run_cycle(base_dir, server)
        assert third.returncode == 0, third.stderr + third.stdout
        assert any(req["path"] == "/2/tweets/search/recent" and req["query"].get("since_id") == ["301"] for req in server.requests)
        state = read_json(base_dir / "bot_state.json")
        assert "302" in state["replied_to_ids"]
        assert state["hot_post_reply_since_ids"]["700"] == "301"

        scenario["search_recent"] = [
            {
                "id": "303",
                "text": "Grok will skip this usable candidate",
                "author_id": "403",
                "conversation_id": "700",
                "referenced_tweets": [{"type": "replied_to", "id": "700"}],
                "created_at": "2026-06-30T12:03:00Z",
            }
        ]
        scenario["grok_replies"] = ["SKIP"]
        fourth = run_cycle(base_dir, server)
        assert fourth.returncode == 0, fourth.stderr + fourth.stdout
        state = read_json(base_dir / "bot_state.json")
        assert state["hot_post_reply_since_ids"]["700"] == "301"
        assert state["skipped_hot_reply_records"]["303"]["reason"] == "no_usable_reply_generated"

        state["hot_post_reply_check_counts"]["700"] = 11
        state["last_reply_epoch"] = 0
        write_json(base_dir / "bot_state.json", state)
        scenario["search_recent"] = [
            {
                "id": "302",
                "text": "Already replied duplicate visible in full rescan",
                "author_id": "402",
                "conversation_id": "700",
                "referenced_tweets": [{"type": "replied_to", "id": "700"}],
                "created_at": "2026-06-30T12:02:00Z",
            }
        ]
        before_posts = len(server.posts)
        before_xai = len(server.xai_requests)
        fifth = run_cycle(base_dir, server)
        assert fifth.returncode == 0, fifth.stderr + fifth.stdout
        recent_searches = [req for req in server.requests if req["path"] == "/2/tweets/search/recent"]
        assert "since_id" not in recent_searches[-1]["query"]
        assert len(server.posts) == before_posts
        assert len(server.xai_requests) == before_xai
    finally:
        server.stop()


def test_duplicate_mention_hot_post_counts_and_state_increment_once(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "duplicate_mention_hot_post.json")).start()
    try:
        base_dir = prepare_base_dir(tmp_path, watch_ids=["700"])
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        assert len(server.xai_requests) == 1
        assert state["daily_reply_count"] == 1
        assert state["daily_replied_author_ids"].count("210") == 1
        assert state["replied_to_ids"].count("110") == 1
    finally:
        server.stop()


def test_state_backup_rotation_and_restart_preserves_reply_state(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "grok_skip.json")).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            watch_ids=["700"],
            local_config={"STATE_BACKUP_COUNT": 2},
            state={
                "daily_reply_count": 3,
                "replied_to_ids": ["old-reply"],
                "hot_post_reply_since_ids": {"700": "555"},
                "skipped_hot_reply_records": {"old-skip": {"reason": "test", "retryable": False, "skipped_epoch": 1}},
            },
        )
        first = run_cycle(base_dir, server)
        assert first.returncode == 0, first.stderr + first.stdout
        assert (base_dir / "bot_state.json.bak1").exists()

        server.scenario["mentions"] = []
        second = run_cycle(base_dir, server)
        assert second.returncode == 0, second.stderr + second.stdout
        assert (base_dir / "bot_state.json.bak2").exists()
        state = read_json(base_dir / "bot_state.json")
        assert "old-reply" in state["replied_to_ids"]
        assert state["hot_post_reply_since_ids"]["700"] == "555"
        assert "old-skip" in state["skipped_hot_reply_records"]
    finally:
        server.stop()


def test_state_recovery_uses_valid_backup_before_default(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(tmp_path, local_config={"STATE_BACKUP_COUNT": 3})
        (base_dir / "bot_state.json").write_text("{bad", encoding="utf-8")
        write_json(
            base_dir / "bot_state.json.bak1",
            {
                "replied_to_ids": ["from-bak1"],
                "daily_reply_date": datetime.now().strftime("%Y-%m-%d"),
                "daily_reply_count": 4,
                "last_seen_mention_id": "99",
                "hot_post_reply_since_ids": {"700": "555"},
                "skipped_hot_reply_records": {"skipped-from-bak1": {"reason": "test", "retryable": False, "skipped_epoch": 1}},
            },
        )
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        assert "from-bak1" in state["replied_to_ids"]
        assert "100" in state["replied_to_ids"]
        assert state["daily_reply_count"] >= 5
        assert state["hot_post_reply_since_ids"]["700"] == "555"
        assert "skipped-from-bak1" in state["skipped_hot_reply_records"]
        assert "Recovered state from backup" in result.stdout
    finally:
        server.stop()


def test_state_recovery_skips_corrupted_backups_and_missing_backups(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(tmp_path, local_config={"STATE_BACKUP_COUNT": 2})
        (base_dir / "bot_state.json").write_text("{bad", encoding="utf-8")
        (base_dir / "bot_state.json.bak1").write_text("{also bad", encoding="utf-8")
        write_json(base_dir / "bot_state.json.bak2", {"replied_to_ids": ["from-bak2"], "last_reply_epoch": 0})
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        assert "from-bak2" in state["replied_to_ids"]

        bak3_base = prepare_base_dir(tmp_path / "bak3", local_config={"STATE_BACKUP_COUNT": 3})
        (bak3_base / "bot_state.json").write_text("{bad", encoding="utf-8")
        (bak3_base / "bot_state.json.bak1").write_text("{bad", encoding="utf-8")
        (bak3_base / "bot_state.json.bak2").write_text("{bad", encoding="utf-8")
        write_json(
            bak3_base / "bot_state.json.bak3",
            {
                "replied_to_ids": ["from-bak3"],
                "daily_reply_count": 2,
                "hot_post_reply_since_ids": {"701": "888"},
                "skipped_hot_reply_records": {"skip-bak3": {"reason": "test", "retryable": False, "skipped_epoch": 1}},
                "last_reply_epoch": 0,
            },
        )
        server.scenario.update(load_scenario(SCENARIOS / "normal_mention_reply.json"))
        server.scenario["mentions"][0]["id"] = "103"
        server.scenario["mentions"][0]["author_id"] = "203"
        server.scenario["grok_replies"] = ["Recovered from bak3."]
        bak3 = run_cycle(bak3_base, server)
        assert bak3.returncode == 0, bak3.stderr + bak3.stdout
        state = read_json(bak3_base / "bot_state.json")
        assert "from-bak3" in state["replied_to_ids"]
        assert state["hot_post_reply_since_ids"]["701"] == "888"
        assert "skip-bak3" in state["skipped_hot_reply_records"]

        missing_base = prepare_base_dir(tmp_path / "missing", local_config={"STATE_BACKUP_COUNT": 2})
        (missing_base / "bot_state.json").write_text("{bad", encoding="utf-8")
        (missing_base / "bot_state.json.bak1").write_text("{bad", encoding="utf-8")
        server.scenario.update(load_scenario(SCENARIOS / "normal_mention_reply.json"))
        server.scenario["mentions"][0]["id"] = "102"
        server.scenario["grok_replies"] = ["Default state after all backups fail."]
        missing = run_cycle(missing_base, server)
        assert missing.returncode == 0, missing.stderr + missing.stdout
        state = read_json(missing_base / "bot_state.json")
        assert "102" in state["replied_to_ids"]
        assert "No usable state file or backup found" in missing.stdout
    finally:
        server.stop()


def test_missing_and_malformed_state_fail_safely(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(tmp_path)
        missing = run_cycle(base_dir, server)
        assert missing.returncode == 0, missing.stderr + missing.stdout
        assert (base_dir / "bot_state.json").exists()

        (base_dir / "bot_state.json").write_text("{not json", encoding="utf-8")
        server.scenario.update(load_scenario(SCENARIOS / "normal_mention_reply.json"))
        server.scenario["mentions"][0]["id"] = "101"
        server.scenario["grok_replies"] = ["Recovered from malformed state."]
        malformed = run_cycle(base_dir, server)
        assert malformed.returncode == 0, malformed.stderr + malformed.stdout
        state = read_json(base_dir / "bot_state.json")
        assert "101" in state["replied_to_ids"]
    finally:
        server.stop()


def test_midnight_rollover_resets_reply_counts_and_spacing_uses_epoch(tmp_path: Path) -> None:
    fake_now = int(datetime(2026, 7, 1, 0, 5).timestamp())
    previous_day = "2026-06-30"
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "daily_reply_date": previous_day,
                "daily_reply_count": 7,
                "daily_replied_author_ids": ["200"],
                "last_reply_epoch": fake_now - 60,
            },
            local_config={"MIN_SECONDS_BETWEEN_REPLIES": 3600},
        )
        spaced = run_bot_command(base_dir, server, extra_env={"MRS_FAKE_NOW_EPOCH": str(fake_now)})
        assert spaced.returncode == 0, spaced.stderr + spaced.stdout
        assert server.posts == []
        state = read_json(base_dir / "bot_state.json")
        assert state["daily_reply_date"] == "2026-07-01"
        assert state["daily_reply_count"] == 0
        assert state["daily_replied_author_ids"] == []

        state["last_reply_epoch"] = 0
        write_json(base_dir / "bot_state.json", state)
        posted = run_bot_command(base_dir, server, extra_env={"MRS_FAKE_NOW_EPOCH": str(fake_now)})
        assert posted.returncode == 0, posted.stderr + posted.stdout
        state = read_json(base_dir / "bot_state.json")
        assert state["daily_reply_count"] == 1
        assert state["daily_replied_author_ids"] == ["200"]
    finally:
        server.stop()


def test_midnight_rollover_resets_quote_counts_and_meme_fallback_date(tmp_path: Path) -> None:
    fake_now_dt = datetime(2026, 7, 1, 23, 30)
    fake_now = int(fake_now_dt.timestamp())
    server = FakeApiServer(load_scenario(SCENARIOS / "quote_tweet_reply.json")).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "next_reply_lane_priority": "quote",
                "recent_own_post_ids": ["900"],
                "daily_quote_reply_date": "2026-06-30",
                "daily_quote_reply_count": 5,
                "daily_reply_date": "2026-06-30",
                "daily_reply_count": 5,
                "last_reply_epoch": 0,
            },
            local_config={"ENABLE_HOT_POST_REPLY_CHECKS": False},
        )
        result = run_bot_command(base_dir, server, extra_env={"MRS_FAKE_NOW_EPOCH": str(fake_now)})
        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        assert state["daily_quote_reply_date"] == "2026-07-01"
        assert state["daily_quote_reply_count"] == 1

        meme_base = prepare_base_dir(
            tmp_path / "meme",
            meme=True,
            local_config={"ENABLE_DAILY_MEME_POSTS": True, "MEME_FALLBACK_HOUR": 16, "MEME_FALLBACK_MINUTE": 0},
        )
        meme_server = FakeApiServer(load_scenario(SCENARIOS / "meme_post.json")).start()
        try:
            meme_result = run_bot_command(meme_base, meme_server, "--test-post-meme", extra_env={"MRS_FAKE_NOW_EPOCH": str(fake_now)})
            assert meme_result.returncode == 0, meme_result.stderr + meme_result.stdout
            meme_state = read_json(meme_base / "bot_state.json")
            assert meme_state["next_meme_schedule_date"] == "2026-07-02"
        finally:
            meme_server.stop()
    finally:
        server.stop()


def test_runtime_control_individual_lanes_malformed_and_expired_pause(tmp_path: Path) -> None:
    quote_server = FakeApiServer(load_scenario(SCENARIOS / "quote_tweet_reply.json")).start()
    try:
        quote_base = prepare_base_dir(
            tmp_path / "quote",
            control={"pause_quote_replies": True},
            state={"next_reply_lane_priority": "quote", "recent_own_post_ids": ["900"], "last_reply_epoch": 0},
            local_config={"ENABLE_HOT_POST_REPLY_CHECKS": False},
        )
        result = run_cycle(quote_base, quote_server)
        assert result.returncode == 0, result.stderr + result.stdout
        assert quote_server.posts == []
    finally:
        quote_server.stop()

    post_server = FakeApiServer(load_scenario(SCENARIOS / "media_quote_post.json")).start()
    try:
        post_base = prepare_base_dir(tmp_path / "post", control={"disable_quote_posts": True})
        result = run_bot_command(post_base, post_server, "--test-post-quote")
        assert result.returncode == 0, result.stderr + result.stdout
        assert post_server.posts == []
    finally:
        post_server.stop()

    malformed_server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        malformed_base = prepare_base_dir(tmp_path / "malformed")
        (malformed_base / "mrsMThatcher.control.json").write_text("{bad json", encoding="utf-8")
        result = run_cycle(malformed_base, malformed_server)
        assert result.returncode == 0, result.stderr + result.stdout
        assert len(malformed_server.posts) == 1
        assert "Failed to read control file" in result.stdout
    finally:
        malformed_server.stop()

    expired_server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        expired_base = prepare_base_dir(tmp_path / "expired", control={"pause_replies_until": "2000-01-01 00:00"})
        result = run_cycle(expired_base, expired_server)
        assert result.returncode == 0, result.stderr + result.stdout
        assert len(expired_server.posts) == 1
    finally:
        expired_server.stop()


def test_local_config_validation_rejects_bad_values_and_cannot_override_paths_or_urls(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            local_config={
                "UNKNOWN_KEY": True,
                "ENABLE_AUTO_REPLIES": "not-a-bool",
                "MIN_SECONDS_BETWEEN_REPLIES": -99,
                "MRS_BASE_DIR": "/should/not/apply",
                "X_API_BASE_URL": "https://api.x.com",
            },
        )
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        assert len(server.posts) == 1
        assert "Ignoring unsupported local config key" in result.stdout
        assert "Ignoring invalid local config override ENABLE_AUTO_REPLIES" in result.stdout
        assert "Ignoring invalid local config override MIN_SECONDS_BETWEEN_REPLIES" in result.stdout
        assert read_json(base_dir / "bot_state.json")["replied_to_ids"] == ["100"]
    finally:
        server.stop()


def test_quote_tweet_bad_watch_id_and_inaccessible_original_do_not_call_grok(tmp_path: Path) -> None:
    server = FakeApiServer({"tweets": {}, "quote_tweets": {}}).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={"next_reply_lane_priority": "quote", "recent_own_post_ids": ["999"], "last_reply_epoch": 0},
            local_config={"ENABLE_HOT_POST_REPLY_CHECKS": False},
        )
        (base_dir / "extra_quote_watch_post_ids.txt").write_text("not-a-post-id\n", encoding="utf-8")
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        assert server.xai_requests == []
        assert server.posts == []
        assert any(req["path"] == "/2/tweets/999" for req in server.requests)
    finally:
        server.stop()


def test_quote_tweets_process_oldest_first_stop_after_one_and_skip_seen(tmp_path: Path) -> None:
    scenario = {
        "tweets": {
            "900": {
                "id": "900",
                "text": "Original own post",
                "author_id": "12345",
                "conversation_id": "900",
                "created_at": "2026-06-30T10:00:00Z",
            }
        },
        "quote_tweets": {
            "900": {
                "data": [
                    {
                        "id": "912",
                        "text": "Second quote",
                        "author_id": "312",
                        "conversation_id": "912",
                        "referenced_tweets": [{"type": "quoted", "id": "900"}],
                        "created_at": "2026-06-30T10:02:00Z",
                    },
                    {
                        "id": "911",
                        "text": "First quote",
                        "author_id": "311",
                        "conversation_id": "911",
                        "referenced_tweets": [{"type": "quoted", "id": "900"}],
                        "created_at": "2026-06-30T10:01:00Z",
                    },
                ],
                "includes": {"users": []},
            }
        },
        "grok_replies": ["Reply to the oldest quote."],
    }
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={"next_reply_lane_priority": "quote", "recent_own_post_ids": ["900"], "last_reply_epoch": 0},
            local_config={"ENABLE_HOT_POST_REPLY_CHECKS": False},
        )
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        assert len(server.posts) == 1
        assert server.posts[0]["reply"]["in_reply_to_tweet_id"] == "911"
        assert len(server.xai_requests) == 1
        state = read_json(base_dir / "bot_state.json")
        assert "911" in state["replied_to_quote_post_ids"]
        assert "912" not in state["replied_to_quote_post_ids"]

        state["last_reply_epoch"] = 0
        state["seen_quote_post_ids"] = ["911", "912"]
        state["replied_to_quote_post_ids"] = []
        state["skipped_quote_post_ids"] = []
        write_json(base_dir / "bot_state.json", state)
        before_xai = len(server.xai_requests)
        scenario["grok_replies"] = ["Should not be used for already seen quote."]
        seen = run_cycle(base_dir, server)
        assert seen.returncode == 0, seen.stderr + seen.stdout
        assert len(server.xai_requests) == before_xai
    finally:
        server.stop()


def test_structured_event_logs_are_valid_json_and_cover_no_post_paths(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "duplicate_mention_hot_post.json")).start()
    try:
        base_dir = prepare_base_dir(tmp_path, watch_ids=["700"])
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        events = event_payloads(base_dir)
        event_names = {event["event"] for event in events}
        assert "reply_posted" in event_names
        assert "hot_search" in event_names
    finally:
        server.stop()

    skip_server = FakeApiServer(load_scenario(SCENARIOS / "grok_skip.json")).start()
    try:
        skip_base = prepare_base_dir(tmp_path / "skip")
        result = run_cycle(skip_base, skip_server)
        assert result.returncode == 0, result.stderr + result.stdout
        events = event_payloads(skip_base)
        skipped = [event for event in events if event["event"] == "candidate_skipped"]
        assert skipped
        assert skipped[0]["reason"] == "no_usable_reply_generated"
    finally:
        skip_server.stop()

    empty_server = FakeApiServer(load_scenario(SCENARIOS / "hot_post_since_id_watermark.json")).start()
    try:
        empty_base = prepare_base_dir(tmp_path / "empty", watch_ids=["700"])
        result = run_cycle(empty_base, empty_server)
        assert result.returncode == 0, result.stderr + result.stdout
        events = event_payloads(empty_base)
        assert any(event["event"] == "hot_search" and event["usable_count"] == 0 for event in events)
        assert any(event["event"] == "quote_check_status" for event in events)
    finally:
        empty_server.stop()


@pytest.mark.parametrize("fake_server", ["duplicate_mention_hot_post.json"], indirect=True)
def test_duplicate_mention_and_hot_post_candidate_posts_once(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path, watch_ids=["700"])
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    assert len(fake_server.posts) == 1
    assert fake_server.posts[0]["reply"]["in_reply_to_tweet_id"] == "110"
    assert len(fake_server.xai_requests) == 1
    state = read_json(base_dir / "bot_state.json")
    assert state["last_seen_mention_id"] == "110"


@pytest.mark.parametrize("fake_server", ["hot_post_since_id_watermark.json"], indirect=True)
def test_hot_post_since_id_watermark_updates_when_no_usable_candidates(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path, watch_ids=["700"])
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.posts == []
    state = read_json(base_dir / "bot_state.json")
    assert state["hot_post_reply_since_ids"]["700"] == "120"


@pytest.mark.parametrize("fake_server", ["quote_tweet_reply.json"], indirect=True)
def test_quote_tweet_reply(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(
        tmp_path,
        state={
            "recent_own_post_ids": ["900"],
            "last_reply_epoch": 0,
            "daily_reply_date": datetime.now().strftime("%Y-%m-%d"),
            "daily_reply_count": 0,
        },
        local_config={"ENABLE_HOT_POST_REPLY_CHECKS": False},
    )
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    assert len(fake_server.posts) == 1
    assert fake_server.posts[0]["reply"]["in_reply_to_tweet_id"] == "910"
    state = read_json(base_dir / "bot_state.json")
    assert "910" in state["replied_to_quote_post_ids"]


@pytest.mark.parametrize("fake_server", ["grok_skip.json"], indirect=True)
def test_grok_skip_does_not_post(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path)
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.posts == []
    state = read_json(base_dir / "bot_state.json")
    assert state["last_seen_mention_id"] == "130"
    assert "130" not in state["replied_to_ids"]


@pytest.mark.parametrize("fake_server", ["per_author_cap.json"], indirect=True)
def test_per_author_cap_skips_second_reply(tmp_path: Path, fake_server: FakeApiServer) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    base_dir = prepare_base_dir(
        tmp_path,
        state={
            "daily_reply_date": today,
            "daily_reply_count": 1,
            "daily_replied_author_ids": ["240"],
            "last_reply_epoch": 0,
        },
    )
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.posts == []
    assert fake_server.xai_requests == []
    state = read_json(base_dir / "bot_state.json")
    assert state["last_seen_mention_id"] == "140"


@pytest.mark.parametrize("fake_server", ["daily_cap.json"], indirect=True)
def test_daily_cap_skips_before_fetching_mentions(tmp_path: Path, fake_server: FakeApiServer) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    base_dir = prepare_base_dir(
        tmp_path,
        local_config={"MAX_AUTO_REPLIES_PER_DAY": 1},
        state={"daily_reply_date": today, "daily_reply_count": 1, "last_reply_epoch": 0},
    )
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.posts == []
    assert not any(req["path"].endswith("/mentions") for req in fake_server.requests)


@pytest.mark.parametrize("fake_server", ["runtime_control_pause.json"], indirect=True)
def test_runtime_control_pause_skips_replies(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path, control={"pause_replies": True})
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.posts == []
    assert not any(req["path"].endswith("/mentions") for req in fake_server.requests)


@pytest.mark.parametrize("fake_server", ["local_config_override.json"], indirect=True)
def test_local_config_override_can_disable_reply_lanes(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(
        tmp_path,
        local_config={"ENABLE_AUTO_REPLIES": False, "ENABLE_QUOTE_TWEET_CHECKS": False},
    )
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.posts == []
    assert fake_server.requests == []


@pytest.mark.parametrize("fake_server", ["api_429_cooldown.json"], indirect=True)
def test_api_429_sets_cooldown_in_test_state(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path)
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    state = read_json(base_dir / "bot_state.json")
    assert state["api_cooldown_reason"] == "x returned 429/rate limit"
    assert int(state["api_cooldown_until_epoch"]) > int(datetime.now().timestamp())
    assert fake_server.posts == []


@pytest.mark.parametrize("fake_server", ["media_quote_post.json"], indirect=True)
def test_quote_image_post_uploads_media_records_state_and_schedules_meme(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(
        tmp_path,
        local_config={
            "ENABLE_DAILY_MEME_POSTS": True,
            "MEME_TRIGGER_AFTER_HOUR": 0,
            "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS": 60,
            "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS": 60,
        },
    )
    result = run_bot_command(base_dir, fake_server, "--test-post-quote")

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.path_counts["/2/media/upload"] == 1
    assert len(fake_server.posts) == 1
    assert fake_server.posts[0]["media"]["media_ids"] == ["fake-media-v2"]
    state = read_json(base_dir / "bot_state.json")
    assert state["last_main_post_id"] == "901000"
    assert state["recent_own_post_ids"] == ["901000"]
    assert state["next_meme_schedule_mode"] == "after_first_quote_after_midday"
    assert int(state["next_meme_post_epoch"]) == int(state["last_quote_post_epoch"]) + 60
    assert (base_dir / "lines_used.pickle").exists()
    assert (base_dir / "images_used.pickle").exists()


@pytest.mark.parametrize("fake_server", ["media_v2_fallback.json"], indirect=True)
def test_media_v2_failure_falls_back_to_v1_upload(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path)
    result = run_bot_command(base_dir, fake_server, "--test-post-quote")

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.path_counts["/2/media/upload"] == 1
    assert fake_server.path_counts["/1.1/media/upload.json"] == 1
    assert fake_server.posts[0]["media"]["media_ids"] == ["fake-media-v1-fallback"]


@pytest.mark.parametrize("fake_server", ["meme_post.json"], indirect=True)
def test_daily_meme_post_uploads_records_and_reschedules(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(
        tmp_path,
        meme=True,
        local_config={
            "ENABLE_DAILY_MEME_POSTS": True,
            "MEME_POST_TEXT": "Test meme post",
            "MEME_FALLBACK_HOUR": 23,
            "MEME_FALLBACK_MINUTE": 0,
        },
    )
    result = run_bot_command(base_dir, fake_server, "--test-post-meme")

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.path_counts["/2/media/upload"] == 1
    assert len(fake_server.posts) == 1
    assert fake_server.posts[0]["text"] == "Test meme post"
    assert fake_server.posts[0]["media"]["media_ids"] == ["fake-media-v2"]
    state = read_json(base_dir / "bot_state.json")
    assert state["last_main_post_id"] == "901200"
    assert state["posted_meme_filenames"] == ["001_test_meme.png"]
    assert state["next_meme_schedule_mode"] == "fallback"
    assert int(state["next_meme_post_epoch"]) > 0


@pytest.mark.parametrize("fake_server", ["made_with_ai_retry.json"], indirect=True)
def test_made_with_ai_post_failure_retries_without_flag(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path, local_config={"MARK_AI_REPLIES_AS_AI": True})
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.path_counts["/2/tweets"] == 2
    assert len(fake_server.posts) == 1
    assert "made_with_ai" not in fake_server.posts[0]
    assert fake_server.posts[0]["reply"]["in_reply_to_tweet_id"] == "180"
    state = read_json(base_dir / "bot_state.json")
    assert "180" in state["replied_to_ids"]


@pytest.mark.parametrize("fake_server", ["reply_not_allowed_403.json"], indirect=True)
def test_reply_not_allowed_403_marks_mention_handled_without_consuming_quota(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path)
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.posts == []
    state = read_json(base_dir / "bot_state.json")
    assert "190" in state["replied_to_ids"]
    assert state["daily_reply_count"] == 0
    assert state["x_error_epochs"] == []


@pytest.mark.parametrize("fake_server", ["non_json_mentions.json"], indirect=True)
def test_non_json_x_response_records_x_error_without_posting(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path)
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    state = read_json(base_dir / "bot_state.json")
    assert len(state["x_error_epochs"]) == 1
    assert fake_server.posts == []


@pytest.mark.parametrize("fake_server", ["repeated_x_errors.json"], indirect=True)
def test_repeated_x_errors_enter_api_cooldown(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path)

    for _ in range(3):
        result = run_cycle(base_dir, fake_server)
        assert result.returncode == 0, result.stderr + result.stdout

    state = read_json(base_dir / "bot_state.json")
    assert len(state["x_error_epochs"]) == 3
    assert state["api_cooldown_reason"] == "too many x API errors in the last hour"
    assert int(state["api_cooldown_until_epoch"]) > int(datetime.now().timestamp())
    assert fake_server.posts == []


@pytest.mark.parametrize("fake_server", ["xai_failure.json"], indirect=True)
def test_xai_failure_records_xai_error_without_posting(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path)
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    state = read_json(base_dir / "bot_state.json")
    assert len(state["xai_error_epochs"]) == 1
    assert state["x_error_epochs"] == []
    assert fake_server.posts == []


def test_test_mode_refuses_production_base_dir(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.update(
        {
            "MRS_TEST_MODE": "1",
            "MRS_BASE_DIR": str(PRODUCTION_BASE_DIR),
            "X_CONSUMER_KEY": "dummy",
            "X_CONSUMER_SECRET": "dummy",
            "X_ACCESS_TOKEN": "dummy",
            "X_ACCESS_SECRET": "dummy",
            "X_MY_USER_ID": "12345",
            "XAI_API_KEY": "dummy",
        }
    )
    result = subprocess.run(
        [sys.executable, str(BOT), "--test-cycle"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 2
    assert "Refusing to run in MRS_TEST_MODE" in result.stderr


def test_test_mode_refuses_production_child_base_dir(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.update(
        {
            "MRS_TEST_MODE": "1",
            "MRS_BASE_DIR": str(PRODUCTION_BASE_DIR / "tests" / "tmp"),
            "X_CONSUMER_KEY": "dummy",
            "X_CONSUMER_SECRET": "dummy",
            "X_ACCESS_TOKEN": "dummy",
            "X_ACCESS_SECRET": "dummy",
            "X_MY_USER_ID": "12345",
            "XAI_API_KEY": "dummy",
        }
    )
    result = subprocess.run(
        [sys.executable, str(BOT), "--test-cycle"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 2
    assert "production BASE_DIR" in result.stderr


def test_test_mode_refuses_production_log_file(tmp_path: Path) -> None:
    base_dir = prepare_base_dir(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "MRS_TEST_MODE": "1",
            "MRS_BASE_DIR": str(base_dir),
            "MRS_LOG_FILE": str(PRODUCTION_BASE_DIR / "test-harness.log"),
            "X_CONSUMER_KEY": "dummy",
            "X_CONSUMER_SECRET": "dummy",
            "X_ACCESS_TOKEN": "dummy",
            "X_ACCESS_SECRET": "dummy",
            "X_MY_USER_ID": "12345",
            "XAI_API_KEY": "dummy",
            "X_API_BASE_URL": "http://127.0.0.1:9",
            "X_UPLOAD_BASE_URL": "http://127.0.0.1:9",
            "XAI_API_BASE_URL": "http://127.0.0.1:9/v1",
        }
    )
    result = subprocess.run(
        [sys.executable, str(BOT), "--test-cycle"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 2
    assert "production LOG_FILE" in result.stderr


def test_test_mode_refuses_default_live_endpoints(tmp_path: Path) -> None:
    base_dir = prepare_base_dir(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "MRS_TEST_MODE": "1",
            "MRS_BASE_DIR": str(base_dir),
            "MRS_LOG_FILE": str(base_dir / "test.log"),
            "X_CONSUMER_KEY": "dummy",
            "X_CONSUMER_SECRET": "dummy",
            "X_ACCESS_TOKEN": "dummy",
            "X_ACCESS_SECRET": "dummy",
            "X_MY_USER_ID": "12345",
            "XAI_API_KEY": "dummy",
        }
    )
    env.pop("X_API_BASE_URL", None)
    env.pop("X_UPLOAD_BASE_URL", None)
    env.pop("XAI_API_BASE_URL", None)

    result = subprocess.run(
        [sys.executable, str(BOT), "--test-cycle"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 2
    assert "Refusing to run in MRS_TEST_MODE with live endpoint" in result.stdout


def test_live_endpoint_override_requires_deliberate_phrase(tmp_path: Path) -> None:
    base_dir = prepare_base_dir(tmp_path)
    accidental = run_bot_with_env(
        base_dir,
        extra_env={
            "MRS_ALLOW_LIVE_ENDPOINTS_IN_TEST": "1",
        },
    )
    assert accidental.returncode == 2
    assert "Refusing to run in MRS_TEST_MODE with live endpoint" in accidental.stdout


@pytest.mark.parametrize("fake_server", ["normal_mention_reply.json"], indirect=True)
def test_live_endpoint_override_exact_phrase_does_not_break_fake_endpoints(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path)
    result = run_bot_command(
        base_dir,
        fake_server,
        extra_env={"MRS_ALLOW_LIVE_ENDPOINTS_IN_TEST": "I_UNDERSTAND_THIS_CAN_POST_TO_LIVE_X"},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert len(fake_server.posts) == 1


def test_self_test_in_test_mode_refuses_each_default_live_endpoint(tmp_path: Path) -> None:
    base_dir = prepare_base_dir(tmp_path, local_config={"MIN_SECONDS_BETWEEN_REPLIES": 1})
    fake = "http://127.0.0.1:9"

    live_x = run_bot_with_env(
        base_dir,
        "--self-test",
        extra_env={
            "X_UPLOAD_BASE_URL": fake,
            "XAI_API_BASE_URL": f"{fake}/v1",
        },
    )
    assert live_x.returncode == 2
    assert "X_API_BASE_URL=https://api.x.com" in live_x.stdout

    live_upload = run_bot_with_env(
        base_dir,
        "--self-test",
        extra_env={
            "X_API_BASE_URL": fake,
            "XAI_API_BASE_URL": f"{fake}/v1",
        },
    )
    assert live_upload.returncode == 2
    assert "X_UPLOAD_BASE_URL=https://upload.twitter.com" in live_upload.stdout

    live_xai = run_bot_with_env(
        base_dir,
        "--self-test",
        extra_env={
            "X_API_BASE_URL": fake,
            "X_UPLOAD_BASE_URL": fake,
        },
    )
    assert live_xai.returncode == 2
    assert "XAI_API_BASE_URL=https://api.x.ai/v1" in live_xai.stdout


def test_self_test_in_test_mode_accepts_fake_endpoints_and_exact_phrase_only(tmp_path: Path) -> None:
    base_dir = prepare_base_dir(tmp_path, local_config={"MIN_SECONDS_BETWEEN_REPLIES": 1})
    fake = "http://127.0.0.1:9"

    fake_endpoints = run_bot_with_env(
        base_dir,
        "--self-test",
        extra_env={
            "X_API_BASE_URL": fake,
            "X_UPLOAD_BASE_URL": fake,
            "XAI_API_BASE_URL": f"{fake}/v1",
        },
    )
    assert fake_endpoints.returncode == 0, fake_endpoints.stderr + fake_endpoints.stdout

    accidental = run_bot_with_env(
        base_dir,
        "--self-test",
        extra_env={
            "MRS_ALLOW_LIVE_ENDPOINTS_IN_TEST": "1",
        },
    )
    assert accidental.returncode == 2
    assert "Refusing to run in MRS_TEST_MODE with live endpoint" in accidental.stdout

    deliberate = run_bot_with_env(
        base_dir,
        "--self-test",
        extra_env={
            "MRS_ALLOW_LIVE_ENDPOINTS_IN_TEST": "I_UNDERSTAND_THIS_CAN_POST_TO_LIVE_X",
        },
    )
    assert deliberate.returncode == 0, deliberate.stderr + deliberate.stdout


@pytest.mark.parametrize("fake_server", ["normal_mention_reply.json"], indirect=True)
def test_endpoint_overrides_tolerate_terminal_version_segments(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path)
    result = run_bot_command(
        base_dir,
        fake_server,
        "--test-cycle",
        x_api_base_url=f"{fake_server.url}/2",
        x_upload_base_url=f"{fake_server.url}/1.1",
        xai_api_base_url=f"{fake_server.url}/v1",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert len(fake_server.posts) == 1
    assert fake_server.posts[0]["reply"]["in_reply_to_tweet_id"] == "100"


@pytest.mark.parametrize(
    "failure,service,path",
    [
        ("closed", "x", "/2/users/12345/mentions"),
        ("timeout", "x", "/2/users/12345/mentions"),
        ("closed", "xai", "/v1/chat/completions"),
        ("timeout", "xai", "/v1/chat/completions"),
    ],
)
def test_network_level_failures_closed_and_timeout(tmp_path: Path, failure: str, service: str, path: str) -> None:
    scenario = {"network_failures": {path: failure}, "network_timeout_sleep_seconds": 1}
    if service == "xai":
        scenario["mentions"] = load_scenario(SCENARIOS / "normal_mention_reply.json")["mentions"]
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(tmp_path)
        result = run_bot_command(base_dir, server, extra_env={"MRS_REQUEST_TIMEOUT_SECONDS": "0.05"})
        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        if service == "x":
            assert len(state["x_error_epochs"]) == 1
            assert state["xai_error_epochs"] == []
        else:
            assert len(state["xai_error_epochs"]) == 1
            assert state["x_error_epochs"] == []
        assert server.posts == []
    finally:
        server.stop()


def test_connection_refused_for_x_and_xai_are_recorded(tmp_path: Path) -> None:
    x_base = prepare_base_dir(tmp_path / "x")
    x_result = run_bot_with_env(
        x_base,
        extra_env={
            "X_API_BASE_URL": "http://127.0.0.1:9",
            "X_UPLOAD_BASE_URL": "http://127.0.0.1:9",
            "XAI_API_BASE_URL": "http://127.0.0.1:9/v1",
            "MRS_REQUEST_TIMEOUT_SECONDS": "0.05",
        },
    )
    assert x_result.returncode == 0, x_result.stderr + x_result.stdout
    assert len(read_json(x_base / "bot_state.json")["x_error_epochs"]) == 1

    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        xai_base = prepare_base_dir(tmp_path / "xai")
        xai_result = run_bot_command(
            xai_base,
            server,
            xai_api_base_url="http://127.0.0.1:9/v1",
            extra_env={"MRS_REQUEST_TIMEOUT_SECONDS": "0.05"},
        )
        assert xai_result.returncode == 0, xai_result.stderr + xai_result.stdout
        state = read_json(xai_base / "bot_state.json")
        assert len(state["xai_error_epochs"]) == 1
        assert state["x_error_epochs"] == []
    finally:
        server.stop()


@pytest.mark.parametrize(
    "scenario_update,expect_xai_error,expect_post",
    [
        ({"xai_non_json": True}, True, False),
        ({"xai_success_body": {}}, True, False),
        ({"xai_success_body": {"choices": [{"message": {}}]}}, True, False),
        ({"xai_success_body": {"choices": [{"message": {"content": ""}}]}}, False, False),
        ({"xai_success_body": {"choices": [{"message": {"content": "word " * 200}}]}}, False, True),
    ],
)
def test_malformed_xai_success_responses(tmp_path: Path, scenario_update: dict, expect_xai_error: bool, expect_post: bool) -> None:
    scenario = load_scenario(SCENARIOS / "normal_mention_reply.json")
    scenario.update(scenario_update)
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(tmp_path)
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        assert bool(state["xai_error_epochs"]) is expect_xai_error
        assert bool(server.posts) is expect_post
        if expect_post:
            assert len(server.posts[0]["text"]) <= 270
    finally:
        server.stop()


def test_no_media_or_missing_files_for_quote_and_meme_posting(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "media_quote_post.json")).start()
    try:
        quote_base = prepare_base_dir(tmp_path / "quote")
        for image in (quote_base / "images").iterdir():
            image.unlink()
        result = run_bot_command(quote_base, server, "--test-post-quote")
        assert result.returncode == 1
        assert server.posts == []

        missing_line_base = prepare_base_dir(tmp_path / "missing-line")
        (missing_line_base / "mrsMThatcher.txt").unlink()
        result = run_bot_command(missing_line_base, server, "--test-post-quote")
        assert result.returncode == 1
        assert server.posts == []

        missing_selected_base = prepare_base_dir(tmp_path / "missing-selected")
        for image in (missing_selected_base / "images").iterdir():
            image.unlink()
        (missing_selected_base / "images" / "t01.jpg").symlink_to(missing_selected_base / "images" / "does-not-exist.jpg")
        result = run_bot_command(missing_selected_base, server, "--test-post-quote")
        assert result.returncode == 1
        assert server.posts == []

        meme_base = prepare_base_dir(tmp_path / "meme", meme=True, local_config={"ENABLE_DAILY_MEME_POSTS": True})
        for meme in (meme_base / "final_posting_queue_top90_as_is" / "images").iterdir():
            meme.unlink()
        result = run_bot_command(meme_base, server, "--test-post-meme")
        assert result.returncode == 0
        assert server.posts == []
        assert int(read_json(meme_base / "bot_state.json")["next_meme_post_epoch"]) > 0

        missing_meme_dir_base = prepare_base_dir(
            tmp_path / "missing-meme-dir",
            local_config={"ENABLE_DAILY_MEME_POSTS": True},
        )
        result = run_bot_command(missing_meme_dir_base, server, "--test-post-meme")
        assert result.returncode == 0
        assert server.posts == []
        assert int(read_json(missing_meme_dir_base / "bot_state.json")["next_meme_post_epoch"]) > 0

        malformed_analysis_base = prepare_base_dir(
            tmp_path / "malformed-analysis",
            meme=True,
            local_config={"ENABLE_DAILY_MEME_POSTS": True, "MEME_POST_TEXT": "meme"},
        )
        (malformed_analysis_base / "final_posting_queue_top90_as_is" / "renamed_png_v3_top90_posting_queue.json").write_text(
            "{bad json",
            encoding="utf-8",
        )
        result = run_bot_command(malformed_analysis_base, server, "--test-post-meme")
        assert result.returncode == 0, result.stderr + result.stdout
        assert len(server.posts) == 1
        assert server.posts[-1]["text"] == "meme"

        all_posted_base = prepare_base_dir(
            tmp_path / "all-posted",
            meme=True,
            local_config={"ENABLE_DAILY_MEME_POSTS": True, "RESET_MEME_CYCLE_WHEN_ALL_POSTED": False},
            state={"posted_meme_filenames": ["001_test_meme.png"]},
        )
        post_count = len(server.posts)
        result = run_bot_command(all_posted_base, server, "--test-post-meme")
        assert result.returncode == 0
        assert len(server.posts) == post_count
        assert int(read_json(all_posted_base / "bot_state.json")["next_meme_post_epoch"]) > 0
    finally:
        server.stop()


def test_api_cooldown_persists_across_restart_with_fake_clock(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "api_429_cooldown.json")).start()
    try:
        base_dir = prepare_base_dir(tmp_path)
        first = run_bot_command(base_dir, server, extra_env={"MRS_FAKE_NOW_EPOCH": "1000"})
        assert first.returncode == 0, first.stderr + first.stdout
        state = read_json(base_dir / "bot_state.json")
        assert state["api_cooldown_until_epoch"] > 1000
        request_count = len(server.requests)

        second = run_bot_command(base_dir, server, extra_env={"MRS_FAKE_NOW_EPOCH": "1100"})
        assert second.returncode == 0, second.stderr + second.stdout
        assert len(server.requests) == request_count
        assert read_json(base_dir / "bot_state.json")["api_cooldown_reason"] == "x returned 429/rate limit"
    finally:
        server.stop()


def test_digest_golden_sections_for_generated_logs(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "duplicate_mention_hot_post.json")).start()
    try:
        base_dir = prepare_base_dir(tmp_path, watch_ids=["700"])
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        digest = run_digest(base_dir, state_file=base_dir / ".digest_state.json")
        assert digest.returncode == 0, digest.stderr
        assert "# MrsMThatcher log digest" in digest.stdout
        assert "mention reply/replies" in digest.stdout
        assert "hot_search" in digest.stdout or "hot-post" in digest.stdout
    finally:
        server.stop()

    skip_server = FakeApiServer(load_scenario(SCENARIOS / "grok_skip.json")).start()
    try:
        skip_base = prepare_base_dir(tmp_path / "skip")
        result = run_cycle(skip_base, skip_server)
        assert result.returncode == 0, result.stderr + result.stdout
        digest = run_digest(skip_base)
        assert digest.returncode == 0, digest.stderr
        assert "candidate_skipped" in (skip_base / "test.log").read_text(encoding="utf-8")
        assert "Grok skip" in digest.stdout or "candidate" in digest.stdout
    finally:
        skip_server.stop()

    quote_server = FakeApiServer(load_scenario(SCENARIOS / "hot_post_since_id_watermark.json")).start()
    try:
        quote_base = prepare_base_dir(tmp_path / "quote-status", watch_ids=["700"])
        result = run_cycle(quote_base, quote_server)
        assert result.returncode == 0, result.stderr + result.stdout
        digest = run_digest(quote_base)
        assert digest.returncode == 0, digest.stderr
        assert "quote_tweet_status_checked" in digest.stdout
    finally:
        quote_server.stop()

    meme_server = FakeApiServer(load_scenario(SCENARIOS / "media_quote_post.json")).start()
    try:
        meme_base = prepare_base_dir(
            tmp_path / "meme-schedule",
            local_config={
                "ENABLE_DAILY_MEME_POSTS": True,
                "MEME_TRIGGER_AFTER_HOUR": 0,
                "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS": 60,
                "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS": 60,
            },
        )
        result = run_bot_command(meme_base, meme_server, "--test-post-quote")
        assert result.returncode == 0, result.stderr + result.stdout
        state_file = meme_base / ".digest_state.json"
        first_digest = run_digest(meme_base, state_file=state_file)
        assert first_digest.returncode == 0, first_digest.stderr
        assert "meme" in first_digest.stdout.lower()
        quiet_digest = run_digest(meme_base, state_file=state_file)
        assert quiet_digest.returncode == 0, quiet_digest.stderr
        assert "Reply budget" in quiet_digest.stdout
    finally:
        meme_server.stop()

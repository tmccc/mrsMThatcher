from __future__ import annotations

import copy
import io
import json
import os
import random
import subprocess
import sys
import tarfile
import time
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


def normalize_for_branch_parity(value):
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            if key in {
                "last_reply_check_epoch",
                "quote_api_cooldown_reason",
                "quote_api_cooldown_until_epoch",
                "quote_x_error_epochs",
            }:
                continue
            if key in {"cached_epoch", "created_at"}:
                continue
            normalized[key] = normalize_for_branch_parity(item)
        if (
            "replied_to_ids" in normalized
            and "next_reply_lane_priority" not in normalized
        ):
            normalized["next_reply_lane_priority"] = "normal"
        return normalized
    if isinstance(value, list):
        return [normalize_for_branch_parity(item) for item in value]
    return value


def run_bot_command_for_root(
    root: Path,
    base_dir: Path,
    server: FakeApiServer,
    command: str,
    *,
    fake_now: str = "2000000000",
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "MRS_TEST_MODE": "1",
            "MRS_FAKE_NOW_EPOCH": fake_now,
            "MRS_BASE_DIR": str(base_dir),
            "MRS_LOG_FILE": str(base_dir / "test.log"),
            "X_API_BASE_URL": server.url,
            "X_UPLOAD_BASE_URL": server.url,
            "XAI_API_BASE_URL": f"{server.url}/v1",
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
    return subprocess.run(
        [sys.executable, str(root / "mrsMThatcher2.py"), command],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def run_branch_parity_case(
    root: Path,
    parent: Path,
    name: str,
    scenario: dict,
    command: str,
    *,
    state: dict | None = None,
    local_config: dict | None = None,
    watch_ids: list[str] | None = None,
    meme: bool = False,
) -> dict:
    server = FakeApiServer(copy.deepcopy(scenario)).start()
    try:
        base_dir = prepare_base_dir(
            parent / name,
            state=copy.deepcopy(state),
            local_config=copy.deepcopy(local_config),
            watch_ids=copy.deepcopy(watch_ids),
            meme=meme,
        )
        result = run_bot_command_for_root(root, base_dir, server, command)
        assert result.returncode == 0, result.stderr + result.stdout
        state_data = read_json(base_dir / "bot_state.json")
        return normalize_for_branch_parity(
            {
                "posts": server.posts,
                "uploads": server.uploads,
                "xai_requests": server.xai_requests,
                "path_counts": dict(server.path_counts),
                "state": state_data,
            }
        )
    finally:
        server.stop()


def test_scheduler_promotion_differential_fuzz_matches_master_except_allowed_scheduler_delta(tmp_path: Path) -> None:
    master_root = tmp_path / "master-archive"
    master_root.mkdir()
    archive = subprocess.check_output(["git", "archive", "master"], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
        tar.extractall(master_root)
    master_has_persisted_normal_scheduler = (
        "last_reply_check_epoch" in (master_root / "mrsMThatcher2.py").read_text(encoding="utf-8")
    )

    rng = random.Random(20260702)
    base_cases = [
        {
            "name": "normal_reply",
            "scenario": load_scenario(SCENARIOS / "normal_mention_reply.json"),
            "command": "--test-cycle",
        },
        {
            "name": "quote_reply",
            "scenario": load_scenario(SCENARIOS / "quote_tweet_reply.json"),
            "command": "--test-cycle",
            "state": {"next_reply_lane_priority": "quote", "recent_own_post_ids": ["900"], "last_reply_epoch": 0},
            "local_config": {"ENABLE_HOT_POST_REPLY_CHECKS": False},
        },
        {
            "name": "duplicate_hot_post",
            "scenario": load_scenario(SCENARIOS / "duplicate_mention_hot_post.json"),
            "command": "--test-cycle",
            "watch_ids": ["700"],
        },
        {
            "name": "grok_skip",
            "scenario": load_scenario(SCENARIOS / "grok_skip.json"),
            "command": "--test-cycle",
        },
        {
            "name": "reply_not_allowed",
            "scenario": load_scenario(SCENARIOS / "reply_not_allowed_403.json"),
            "command": "--test-cycle",
        },
        {
            "name": "non_json_mentions",
            "scenario": load_scenario(SCENARIOS / "non_json_mentions.json"),
            "command": "--test-cycle",
        },
        {
            "name": "xai_failure",
            "scenario": load_scenario(SCENARIOS / "xai_failure.json"),
            "command": "--test-cycle",
        },
        {
            "name": "api_429_cooldown",
            "scenario": load_scenario(SCENARIOS / "api_429_cooldown.json"),
            "command": "--test-cycle",
        },
        {
            "name": "post_quote",
            "scenario": {"next_post_id": 950000},
            "command": "--test-post-quote",
            "local_config": {"POST_SLEEP_MIN": 7200, "POST_SLEEP_MAX": 7200},
        },
        {
            "name": "post_meme",
            "scenario": {"next_post_id": 960000},
            "command": "--test-post-meme",
            "meme": True,
            "local_config": {"ENABLE_DAILY_MEME_POSTS": True, "MEME_POST_TEXT": "meme"},
        },
        {
            "name": "main_tick_due",
            "scenario": load_scenario(SCENARIOS / "normal_mention_reply.json"),
            "command": "--test-main-tick",
            "state": {"last_reply_epoch": 0},
            "local_config": {
                "ENABLE_QUOTE_TWEET_CHECKS": False,
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "REPLY_CHECK_EVERY_SECONDS": 900,
            },
        },
    ]

    fuzz_cases = []
    for index in range(20):
        mention_id = str(10_000 + index)
        author_id = str(20_000 + rng.randrange(8))
        mode = rng.choice(["normal", "skip", "spam", "empty"])
        scenario: dict = {"mentions": []}
        if mode != "empty":
            text = "@mrsMThatcher buy crypto now http://spam.invalid" if mode == "spam" else f"@mrsMThatcher generated case {index}"
            scenario["mentions"] = [
                {
                    "id": mention_id,
                    "text": text,
                    "author_id": author_id,
                    "conversation_id": mention_id,
                    "created_at": "2026-06-30T12:00:00Z",
                }
            ]
        if mode == "normal":
            scenario["grok_replies"] = [f"Generated parity reply {index}."]
        elif mode == "skip":
            scenario["grok_replies"] = ["SKIP"]
        else:
            scenario["grok_replies"] = ["This should not be used."]

        fuzz_cases.append(
            {
                "name": f"fuzz_{index}_{mode}",
                "scenario": scenario,
                "command": rng.choice(["--test-cycle", "--test-main-tick"]),
                "state": {"last_reply_epoch": 0},
                "local_config": {
                    "ENABLE_QUOTE_TWEET_CHECKS": False,
                    "ENABLE_HOT_POST_REPLY_CHECKS": False,
                    "REPLY_CHECK_EVERY_SECONDS": 900,
                },
            }
        )

    for case in base_cases + fuzz_cases:
        master_result = run_branch_parity_case(master_root, tmp_path / "master-runs", **case)
        promotion_result = run_branch_parity_case(ROOT, tmp_path / "promotion-runs", **case)
        assert promotion_result == master_result, case["name"]

    server_master = FakeApiServer({}).start()
    server_promotion = FakeApiServer({}).start()
    try:
        local_config = {
            "ENABLE_QUOTE_TWEET_CHECKS": False,
            "ENABLE_HOT_POST_REPLY_CHECKS": False,
            "REPLY_CHECK_EVERY_SECONDS": 900,
        }
        master_base = prepare_base_dir(tmp_path / "master-expected-diff", state={"last_reply_epoch": 0}, local_config=local_config)
        promotion_base = prepare_base_dir(tmp_path / "promotion-expected-diff", state={"last_reply_epoch": 0}, local_config=local_config)
        for fake_now in ["2000000000", "2000000100"]:
            master_result = run_bot_command_for_root(master_root, master_base, server_master, "--test-main-tick", fake_now=fake_now)
            promotion_result = run_bot_command_for_root(ROOT, promotion_base, server_promotion, "--test-main-tick", fake_now=fake_now)
            assert master_result.returncode == 0, master_result.stderr + master_result.stdout
            assert promotion_result.returncode == 0, promotion_result.stderr + promotion_result.stdout

        expected_master_mentions = 1 if master_has_persisted_normal_scheduler else 2
        assert server_master.path_counts.get("/2/users/12345/mentions") == expected_master_mentions
        assert server_promotion.path_counts.get("/2/users/12345/mentions") == 1
    finally:
        server_master.stop()
        server_promotion.stop()


def test_production_daemon_entrypoint_starts_under_fake_endpoints(tmp_path: Path) -> None:
    server = FakeApiServer({}).start()
    proc: subprocess.Popen[str] | None = None
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 2_000_000_000,
                "last_quote_tweet_check_epoch": 2_000_000_000,
                "next_quote_post_epoch": 2_000_003_600,
            },
            local_config={
                "ENABLE_AUTO_REPLIES": False,
                "ENABLE_QUOTE_TWEET_CHECKS": False,
                "ENABLE_DAILY_MEME_POSTS": False,
                "MIN_SECONDS_BETWEEN_REPLIES": 1,
            },
        )
        env = os.environ.copy()
        env.update(
            {
                "MRS_TEST_MODE": "1",
                "MRS_FAKE_NOW_EPOCH": "2000000000",
                "MRS_BASE_DIR": str(base_dir),
                "MRS_LOG_FILE": str(base_dir / "test.log"),
                "X_API_BASE_URL": server.url,
                "X_UPLOAD_BASE_URL": server.url,
                "XAI_API_BASE_URL": f"{server.url}/v1",
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

        proc = subprocess.Popen(
            [sys.executable, str(BOT)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        log_path = base_dir / "test.log"
        for _ in range(50):
            if log_path.exists() and "Bot started successfully" in log_path.read_text(encoding="utf-8", errors="replace"):
                break
            assert proc.poll() is None
            time.sleep(0.1)
        else:
            raise AssertionError("daemon did not reach startup marker")

        assert proc.poll() is None
        assert server.requests == []
        state = read_json(base_dir / "bot_state.json")
        assert state["next_quote_post_epoch"] == 2_000_003_600
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
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
    assert state["next_reply_lane_priority"] == "quote"
    assert not (PRODUCTION_BASE_DIR / "bot_state.json.tmp").exists()


def test_dry_run_mention_reply_caches_generated_reply_without_posting(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(tmp_path, local_config={"DRY_RUN_REPLIES": True})
        result = run_cycle(base_dir, server)

        assert result.returncode == 0, result.stderr + result.stdout
        assert server.posts == []
        state = read_json(base_dir / "bot_state.json")
        cached = state["tweet_cache"]["dry-run-reply-100"]
        assert cached["text"] == "Quite right. Good sense is unfashionable only to those profiting from nonsense."
        assert cached["author_id"] == "12345"
        assert cached["referenced_tweets"] == [{"id": "100", "type": "replied_to"}]
        assert cached["post_type"] == "auto_reply"
    finally:
        server.stop()


def test_malformed_mention_ids_are_skipped_without_crashing(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIOS / "normal_mention_reply.json")
    scenario["mentions"] = [
        {
            "id": "not-a-tweet-id",
            "text": "@mrsMThatcher malformed",
            "author_id": "201",
            "conversation_id": "not-a-tweet-id",
            "created_at": "2026-06-30T11:59:00Z",
        },
        {
            "id": "101",
            "text": "@mrsMThatcher valid",
            "author_id": "202",
            "conversation_id": "101",
            "created_at": "2026-06-30T12:00:00Z",
        },
    ]
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(tmp_path)
        result = run_cycle(base_dir, server)

        assert result.returncode == 0, result.stderr + result.stdout
        assert len(server.posts) == 1
        assert server.posts[0]["reply"]["in_reply_to_tweet_id"] == "101"
        state = read_json(base_dir / "bot_state.json")
        assert "101" in state["replied_to_ids"]
        assert "not-a-tweet-id" not in state["tweet_cache"]
    finally:
        server.stop()


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


def test_dry_run_quote_reply_caches_generated_reply_without_posting(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "quote_tweet_reply.json")).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={"next_reply_lane_priority": "quote", "recent_own_post_ids": ["900"], "last_reply_epoch": 0},
            local_config={"DRY_RUN_REPLIES": True, "ENABLE_HOT_POST_REPLY_CHECKS": False},
        )
        result = run_cycle(base_dir, server)

        assert result.returncode == 0, result.stderr + result.stdout
        assert server.posts == []
        state = read_json(base_dir / "bot_state.json")
        cached = state["tweet_cache"]["dry-run-quote-reply-910"]
        assert cached["text"] == "A point is useful only when it survives contact with reality. This one rather does."
        assert cached["author_id"] == "12345"
        assert cached["referenced_tweets"] == [{"id": "910", "type": "replied_to"}]
        assert cached["post_type"] == "auto_reply"
    finally:
        server.stop()


def test_production_tick_quote_priority_runs_quote_before_due_mentions(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    scenario["mentions"] = [
        {
            "id": "100",
            "text": "@mrsMThatcher quite right",
            "author_id": "200",
            "conversation_id": "100",
            "created_at": "2026-06-30T12:00:00Z",
        }
    ]
    scenario["grok_replies"] = [
        "A point is useful only when it survives contact with reality. This one rather does.",
        "Quite right. Good sense is unfashionable only to those profiting from nonsense.",
    ]
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "next_reply_lane_priority": "quote",
                "recent_own_post_ids": ["900"],
                "last_reply_epoch": 0,
                "last_quote_tweet_check_epoch": 0,
            },
            local_config={
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "REPLY_CHECK_EVERY_SECONDS": 1,
                "QUOTE_CHECK_EVERY_SECONDS": 1,
            },
        )
        result = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )
        assert result.returncode == 0, result.stderr + result.stdout
        assert len(server.posts) == 1
        assert server.posts[0]["reply"]["in_reply_to_tweet_id"] == "910"
        state = read_json(base_dir / "bot_state.json")
        assert state["next_reply_lane_priority"] == "normal"
        assert state.get("last_seen_mention_id") is None
    finally:
        server.stop()


def test_production_tick_spacing_skip_does_not_consume_normal_check_interval(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "last_reply_epoch": 1_999_999_900,
                "last_reply_check_epoch": 1_999_999_000,
            },
            local_config={
                "ENABLE_QUOTE_TWEET_CHECKS": False,
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "MIN_SECONDS_BETWEEN_REPLIES": 3600,
                "REPLY_CHECK_EVERY_SECONDS": 900,
            },
        )
        result = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )

        assert result.returncode == 0, result.stderr + result.stdout
        assert server.requests == []
        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_check_epoch"] == 1_999_999_000
    finally:
        server.stop()


def test_production_tick_persists_normal_check_epoch_across_restart(tmp_path: Path) -> None:
    server = FakeApiServer({}).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
            },
            local_config={
                "ENABLE_QUOTE_TWEET_CHECKS": False,
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "REPLY_CHECK_EVERY_SECONDS": 900,
            },
        )
        first = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )
        assert first.returncode == 0, first.stderr + first.stdout
        assert server.path_counts.get("/2/users/12345/mentions") == 1
        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_check_epoch"] == 2_000_000_000

        second = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000100"},
        )
        assert second.returncode == 0, second.stderr + second.stdout
        assert server.path_counts.get("/2/users/12345/mentions") == 1
        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_check_epoch"] == 2_000_000_000
    finally:
        server.stop()


def test_restart_after_spacing_skip_polls_when_spacing_opens(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "last_reply_epoch": 1_999_999_900,
                "last_reply_check_epoch": 1_999_999_000,
            },
            local_config={
                "ENABLE_QUOTE_TWEET_CHECKS": False,
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "MIN_SECONDS_BETWEEN_REPLIES": 3600,
                "REPLY_CHECK_EVERY_SECONDS": 900,
            },
        )
        skipped = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )
        assert skipped.returncode == 0, skipped.stderr + skipped.stdout
        assert server.requests == []
        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_check_epoch"] == 1_999_999_000

        spacing_open = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000003500"},
        )
        assert spacing_open.returncode == 0, spacing_open.stderr + spacing_open.stdout
        assert server.path_counts.get("/2/users/12345/mentions") == 1
        assert len(server.posts) == 1
        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_check_epoch"] == 2_000_003_500
    finally:
        server.stop()


def test_restart_after_persisted_normal_check_polls_when_interval_expires(tmp_path: Path) -> None:
    server = FakeApiServer({}).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
            },
            local_config={
                "ENABLE_QUOTE_TWEET_CHECKS": False,
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "REPLY_CHECK_EVERY_SECONDS": 900,
            },
        )
        first = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )
        assert first.returncode == 0, first.stderr + first.stdout
        assert server.path_counts.get("/2/users/12345/mentions") == 1

        second = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000900"},
        )
        assert second.returncode == 0, second.stderr + second.stdout
        assert server.path_counts.get("/2/users/12345/mentions") == 2
        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_check_epoch"] == 2_000_000_900
    finally:
        server.stop()


def test_missing_normal_check_epoch_behaves_like_live_state_and_polls(tmp_path: Path) -> None:
    server = FakeApiServer({}).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={"last_reply_epoch": 0},
            local_config={
                "ENABLE_QUOTE_TWEET_CHECKS": False,
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "REPLY_CHECK_EVERY_SECONDS": 900,
            },
        )

        result = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )

        assert result.returncode == 0, result.stderr + result.stdout
        assert server.path_counts.get("/2/users/12345/mentions") == 1
        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_check_epoch"] == 2_000_000_000
    finally:
        server.stop()


@pytest.mark.parametrize("bad_value", ["not-an-epoch", -100, 2_000_009_999])
def test_bad_normal_check_epoch_falls_back_safely_and_polls(tmp_path: Path, bad_value: object) -> None:
    server = FakeApiServer({}).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "last_reply_epoch": 0,
                "last_reply_check_epoch": bad_value,
            },
            local_config={
                "ENABLE_QUOTE_TWEET_CHECKS": False,
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "REPLY_CHECK_EVERY_SECONDS": 900,
            },
        )

        result = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )

        assert result.returncode == 0, result.stderr + result.stdout
        assert server.path_counts.get("/2/users/12345/mentions") == 1
        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_check_epoch"] == 2_000_000_000
    finally:
        server.stop()


@pytest.mark.parametrize("bad_value", ["not-an-epoch", -100])
def test_bad_quote_check_epoch_falls_back_safely_and_polls(tmp_path: Path, bad_value: object) -> None:
    server = FakeApiServer(
        {
            "tweets": {
                "900": {
                    "id": "900",
                    "text": "Original watched post.",
                    "author_id": "12345",
                    "conversation_id": "900",
                    "created_at": "2026-06-01T07:00:00Z",
                }
            },
            "quote_tweets": {"900": {}},
        }
    ).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "next_reply_lane_priority": "quote",
                "recent_own_post_ids": ["900"],
                "last_reply_epoch": 0,
                "last_quote_tweet_check_epoch": bad_value,
            },
            local_config={
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "QUOTE_CHECK_EVERY_SECONDS": 900,
            },
        )

        result = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )

        assert result.returncode == 0, result.stderr + result.stdout
        assert server.path_counts.get("/2/tweets/900/quote_tweets") == 1
        state = read_json(base_dir / "bot_state.json")
        assert state["last_quote_tweet_check_epoch"] == 2_000_000_000
    finally:
        server.stop()


def test_runtime_disabled_normal_lane_consumes_check_interval(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
            },
            control={"disable_normal_replies": True},
            local_config={
                "ENABLE_QUOTE_TWEET_CHECKS": False,
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "REPLY_CHECK_EVERY_SECONDS": 900,
            },
        )

        result = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )

        assert result.returncode == 0, result.stderr + result.stdout
        assert server.requests == []
        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_check_epoch"] == 2_000_000_000
        assert state.get("replied_to_ids", []) == []
    finally:
        server.stop()


@pytest.mark.parametrize(
    ("scenario", "expected_id", "expected_state_key"),
    [
        ("grok_skip.json", "130", "last_seen_mention_id"),
        ("reply_not_allowed_403.json", "190", "replied_to_ids"),
    ],
)
def test_non_posting_normal_lane_outcomes_consume_check_interval(
    tmp_path: Path,
    scenario: str,
    expected_id: str,
    expected_state_key: str,
) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / scenario)).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
            },
            local_config={
                "ENABLE_QUOTE_TWEET_CHECKS": False,
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "REPLY_CHECK_EVERY_SECONDS": 900,
            },
        )

        first = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )
        second = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000100"},
        )

        assert first.returncode == 0, first.stderr + first.stdout
        assert second.returncode == 0, second.stderr + second.stdout
        assert len(server.posts) == 0
        assert server.path_counts.get("/2/users/12345/mentions") == 1
        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_check_epoch"] == 2_000_000_000
        if expected_state_key == "last_seen_mention_id":
            assert state["last_seen_mention_id"] == expected_id
        else:
            assert expected_id in state[expected_state_key]
    finally:
        server.stop()


def test_spam_normal_lane_outcome_consumes_check_interval(tmp_path: Path) -> None:
    server = FakeApiServer(
        {
            "mentions": [
                {
                    "id": "140",
                    "text": "@mrsMThatcher buy crypto now http://spam.invalid",
                    "author_id": "240",
                    "conversation_id": "140",
                    "created_at": "2026-06-30T12:00:00Z",
                }
            ],
            "grok_replies": ["This should not be used."],
        }
    ).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
            },
            local_config={
                "ENABLE_QUOTE_TWEET_CHECKS": False,
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "REPLY_CHECK_EVERY_SECONDS": 900,
            },
        )

        first = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )
        second = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000100"},
        )

        assert first.returncode == 0, first.stderr + first.stdout
        assert second.returncode == 0, second.stderr + second.stdout
        assert len(server.posts) == 0
        assert len(server.xai_requests) == 0
        assert server.path_counts.get("/2/users/12345/mentions") == 1
        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_check_epoch"] == 2_000_000_000
        assert state["last_seen_mention_id"] == "140"
    finally:
        server.stop()


def test_per_author_cap_normal_lane_outcome_consumes_check_interval(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "daily_reply_date": "2026-05-18",
                "daily_replied_author_counts": {"200": 1},
                "daily_replied_author_ids": ["200"],
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
            },
            local_config={
                "ENABLE_QUOTE_TWEET_CHECKS": False,
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "MAX_REPLIES_PER_AUTHOR_PER_DAY": 1,
                "REPLY_CHECK_EVERY_SECONDS": 900,
            },
        )

        first = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "1779102000"},
        )
        second = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "1779102100"},
        )

        assert first.returncode == 0, first.stderr + first.stdout
        assert second.returncode == 0, second.stderr + second.stdout
        assert len(server.posts) == 0
        assert len(server.xai_requests) == 0
        assert server.path_counts.get("/2/users/12345/mentions") == 1
        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_check_epoch"] == 1_779_102_000
        assert state["last_seen_mention_id"] == "100"
    finally:
        server.stop()


def test_api_cooldown_consumes_normal_check_interval_without_polling(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
                "api_cooldown_until_epoch": 2_000_001_000,
                "api_cooldown_reason": "x",
            },
            local_config={
                "ENABLE_QUOTE_TWEET_CHECKS": False,
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "REPLY_CHECK_EVERY_SECONDS": 900,
            },
        )

        result = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )

        assert result.returncode == 0, result.stderr + result.stdout
        assert server.requests == []
        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_check_epoch"] == 2_000_000_000
        assert state["api_cooldown_until_epoch"] == 2_000_001_000
    finally:
        server.stop()


def test_api_failure_consumes_normal_check_interval_and_records_cooldown(tmp_path: Path) -> None:
    server = FakeApiServer({"error_paths": {"/2/users/12345/mentions": 503}}).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
            },
            local_config={
                "ENABLE_QUOTE_TWEET_CHECKS": False,
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "REPLY_CHECK_EVERY_SECONDS": 900,
            },
        )

        result = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )

        assert result.returncode == 0, result.stderr + result.stdout
        assert server.path_counts.get("/2/users/12345/mentions") == 1
        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_check_epoch"] == 2_000_000_000
        assert state["x_error_epochs"]
        assert state["api_cooldown_until_epoch"] == 0
    finally:
        server.stop()


def test_quote_lookup_errors_cool_down_quote_lane_only(tmp_path: Path) -> None:
    server = FakeApiServer(
        {
            "tweets": {
                "900": {
                    "id": "900",
                    "text": "Original watched post.",
                    "author_id": "12345",
                    "conversation_id": "900",
                    "created_at": "2026-06-01T07:00:00Z",
                }
            },
            "mentions": [
                {
                    "id": "100",
                    "text": "@mrsMThatcher normal lane should still work",
                    "author_id": "200",
                    "conversation_id": "100",
                    "created_at": "2026-06-30T12:00:00Z",
                }
            ],
            "grok_replies": ["The point is plain enough."],
            "error_paths": {"/2/tweets/900/quote_tweets": 503},
        }
    ).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "next_reply_lane_priority": "quote",
                "recent_own_post_ids": ["900"],
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
                "last_quote_tweet_check_epoch": 0,
            },
            local_config={
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "REPLY_CHECK_EVERY_SECONDS": 1,
                "QUOTE_CHECK_EVERY_SECONDS": 1,
                "MIN_SECONDS_BETWEEN_REPLIES": 0,
            },
        )

        for epoch in ["2000000000", "2000000001", "2000000002", "2000000003"]:
            result = run_bot_command(
                base_dir,
                server,
                "--test-main-tick",
                extra_env={"MRS_FAKE_NOW_EPOCH": epoch},
            )
            assert result.returncode == 0, result.stderr + result.stdout

        assert server.path_counts.get("/2/tweets/900/quote_tweets") == 3
        assert server.path_counts.get("/2/users/12345/mentions") == 4
        assert len(server.posts) == 1
        assert server.posts[0]["reply"]["in_reply_to_tweet_id"] == "100"

        state = read_json(base_dir / "bot_state.json")
        assert len(state["quote_x_error_epochs"]) == 3
        assert state["quote_api_cooldown_until_epoch"] == 2_000_003_602
        assert state["quote_api_cooldown_reason"] == "too many quote/x API errors in the last hour"
        assert state["x_error_epochs"] == []
        assert state["api_cooldown_until_epoch"] == 0
        assert "100" in state["replied_to_ids"]
    finally:
        server.stop()


def test_restart_normal_priority_posts_once_then_suppresses_duplicate_poll(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    scenario["mentions"] = [
        {
            "id": "100",
            "text": "@mrsMThatcher quite right",
            "author_id": "200",
            "conversation_id": "100",
            "created_at": "2026-06-30T12:00:00Z",
        }
    ]
    scenario["grok_replies"] = [
        "Quite right. Good sense is unfashionable only to those profiting from nonsense.",
        "The list grows longer each time they need another pound.",
    ]
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "next_reply_lane_priority": "normal",
                "recent_own_post_ids": ["900"],
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
                "last_quote_tweet_check_epoch": 0,
            },
            local_config={
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "REPLY_CHECK_EVERY_SECONDS": 900,
                "QUOTE_CHECK_EVERY_SECONDS": 900,
                "MIN_SECONDS_BETWEEN_REPLIES": 3600,
            },
        )

        first = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )
        assert first.returncode == 0, first.stderr + first.stdout
        assert len(server.posts) == 1
        assert server.posts[0]["reply"]["in_reply_to_tweet_id"] == "100"
        state = read_json(base_dir / "bot_state.json")
        assert state["next_reply_lane_priority"] == "quote"
        assert state["last_reply_check_epoch"] == 2_000_000_000
        assert state["last_quote_tweet_check_epoch"] == 0

        restarted = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000100"},
        )
        assert restarted.returncode == 0, restarted.stderr + restarted.stdout
        assert len(server.posts) == 1
        assert server.path_counts.get("/2/users/12345/mentions") == 1
        state = read_json(base_dir / "bot_state.json")
        assert state["next_reply_lane_priority"] == "quote"
    finally:
        server.stop()


def test_restart_quote_priority_posts_once_then_suppresses_duplicate_quote(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    scenario["mentions"] = [
        {
            "id": "100",
            "text": "@mrsMThatcher quite right",
            "author_id": "200",
            "conversation_id": "100",
            "created_at": "2026-06-30T12:00:00Z",
        }
    ]
    scenario["grok_replies"] = [
        "The list grows longer each time they need another pound.",
        "Quite right. Good sense is unfashionable only to those profiting from nonsense.",
    ]
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "next_reply_lane_priority": "quote",
                "recent_own_post_ids": ["900"],
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
                "last_quote_tweet_check_epoch": 0,
            },
            local_config={
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "REPLY_CHECK_EVERY_SECONDS": 900,
                "QUOTE_CHECK_EVERY_SECONDS": 900,
                "MIN_SECONDS_BETWEEN_REPLIES": 3600,
            },
        )

        first = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )
        assert first.returncode == 0, first.stderr + first.stdout
        assert len(server.posts) == 1
        assert server.posts[0]["reply"]["in_reply_to_tweet_id"] == "910"
        state = read_json(base_dir / "bot_state.json")
        assert state["next_reply_lane_priority"] == "normal"
        assert state["last_quote_tweet_check_epoch"] == 2_000_000_000

        restarted = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000100"},
        )
        assert restarted.returncode == 0, restarted.stderr + restarted.stdout
        assert len(server.posts) == 1
        assert server.path_counts.get("/2/tweets/900/quote_tweets") == 1
        state = read_json(base_dir / "bot_state.json")
        assert state["next_reply_lane_priority"] == "normal"
    finally:
        server.stop()


def test_lane_priority_survives_restart_when_no_reply_posts(tmp_path: Path) -> None:
    server = FakeApiServer({}).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "next_reply_lane_priority": "quote",
                "recent_own_post_ids": ["900"],
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
                "last_quote_tweet_check_epoch": 0,
            },
            local_config={
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "REPLY_CHECK_EVERY_SECONDS": 900,
                "QUOTE_CHECK_EVERY_SECONDS": 900,
            },
        )

        first = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )
        second = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000100"},
        )

        assert first.returncode == 0, first.stderr + first.stdout
        assert second.returncode == 0, second.stderr + second.stdout
        assert len(server.posts) == 0
        state = read_json(base_dir / "bot_state.json")
        assert state["next_reply_lane_priority"] == "quote"
    finally:
        server.stop()


def test_fake_clock_sequence_does_not_duplicate_or_overpoll(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    scenario["mentions"] = [
        {
            "id": "100",
            "text": "@mrsMThatcher quite right",
            "author_id": "200",
            "conversation_id": "100",
            "created_at": "2026-06-30T12:00:00Z",
        }
    ]
    scenario["grok_replies"] = [
        "Quite right. Good sense is unfashionable only to those profiting from nonsense.",
        "The list grows longer each time they need another pound.",
        "This spare reply must never be posted.",
    ]
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "next_reply_lane_priority": "normal",
                "recent_own_post_ids": ["900"],
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
                "last_quote_tweet_check_epoch": 0,
            },
            local_config={
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "REPLY_CHECK_EVERY_SECONDS": 900,
                "QUOTE_CHECK_EVERY_SECONDS": 900,
                "MIN_SECONDS_BETWEEN_REPLIES": 1200,
            },
        )

        for offset in range(0, 7201, 300):
            result = run_bot_command(
                base_dir,
                server,
                "--test-main-tick",
                extra_env={"MRS_FAKE_NOW_EPOCH": str(2_000_000_000 + offset)},
            )
            assert result.returncode == 0, result.stderr + result.stdout

        reply_targets = [post.get("reply", {}).get("in_reply_to_tweet_id") for post in server.posts]
        assert reply_targets.count("100") == 1
        assert reply_targets.count("910") == 1
        assert len(server.posts) == 2
        assert server.path_counts.get("/2/users/12345/mentions", 0) <= 8
        assert server.path_counts.get("/2/tweets/900/quote_tweets", 0) <= 8
        state = read_json(base_dir / "bot_state.json")
        assert state["next_reply_lane_priority"] == "normal"
        assert state["replied_to_ids"].count("100") == 1
        assert state["replied_to_quote_post_ids"].count("910") == 1
    finally:
        server.stop()


def test_two_day_fake_clock_soak_with_posts_pauses_cooldown_and_rollover(tmp_path: Path) -> None:
    scenario = {
        "mentions": [
            {
                "id": "100",
                "text": "@mrsMThatcher quite right",
                "author_id": "200",
                "conversation_id": "100",
                "created_at": "2026-06-30T12:00:00Z",
            },
            {
                "id": "101",
                "text": "@mrsMThatcher what happens next?",
                "author_id": "201",
                "conversation_id": "101",
                "created_at": "2026-07-01T12:00:00Z",
            },
        ],
        "quote_tweets": {
            "900": {
                "data": [
                    {
                        "id": "910",
                        "text": "@mrsMThatcher ? What will you tax next in your levelling down quest?",
                        "author_id": "310",
                        "conversation_id": "910",
                        "referenced_tweets": [{"type": "quoted", "id": "900"}],
                        "created_at": "2026-06-01T08:00:00Z",
                    },
                    {
                        "id": "911",
                        "text": "@mrsMThatcher is the second day any better?",
                        "author_id": "311",
                        "conversation_id": "911",
                        "referenced_tweets": [{"type": "quoted", "id": "900"}],
                        "created_at": "2026-06-02T08:00:00Z",
                    },
                ]
            }
        },
        "tweets": {
            "900": {
                "id": "900",
                "text": "Original watched post.",
                "author_id": "12345",
                "conversation_id": "900",
                "created_at": "2026-06-01T07:00:00Z",
            },
            "300": {
                "id": "300",
                "text": "A usable hot-post candidate",
                "author_id": "400",
                "conversation_id": "900",
                "referenced_tweets": [{"type": "replied_to", "id": "900"}],
                "created_at": "2026-06-01T09:00:00Z",
            }
        },
        "search_recent": [
            {
                "id": "300",
                "text": "A usable hot-post candidate",
                "author_id": "400",
                "conversation_id": "900",
                "referenced_tweets": [{"type": "replied_to", "id": "900"}],
                "created_at": "2026-06-01T09:00:00Z",
            }
        ],
        "grok_replies": [
            "Quite right. Good sense is unfashionable only to those profiting from nonsense.",
            "The list grows longer each time they need another pound.",
            "The next thing is usually dearer government and less liberty.",
            "A second day does not improve a bad idea.",
            "Hot air is not policy, however loudly it is reheated.",
            "This spare reply should never be needed.",
        ],
        "next_post_id": 950000,
    }
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            meme=True,
            watch_ids=["900"],
            state={
                "next_reply_lane_priority": "normal",
                "recent_own_post_ids": ["900"],
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
                "last_quote_tweet_check_epoch": 0,
                "last_seen_mention_id": None,
                "next_quote_post_epoch": 0,
                "next_meme_post_epoch": 0,
                "posted_meme_filenames": [],
                "daily_reply_date": "2030-01-01",
                "daily_reply_count": 99,
                "daily_quote_reply_date": "2030-01-01",
                "daily_quote_reply_count": 99,
                "daily_replied_author_ids": ["stale-author"],
                "daily_replied_author_counts": {"stale-author": 1},
            },
            local_config={
                "ENABLE_HOT_POST_REPLY_CHECKS": True,
                "ENABLE_DAILY_MEME_POSTS": True,
                "REPLY_CHECK_EVERY_SECONDS": 900,
                "QUOTE_CHECK_EVERY_SECONDS": 900,
                "MIN_SECONDS_BETWEEN_REPLIES": 1800,
                "MAX_AUTO_REPLIES_PER_DAY": 4,
                "MAX_QUOTE_REPLIES_PER_DAY": 2,
                "MAX_REPLIES_PER_AUTHOR_PER_DAY": 1,
                "MEME_POST_TEXT": "meme",
                "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS": 2100,
                "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS": 2100,
            },
        )

        tick_epochs = [
            1_780_370_100,  # 2026-06-01 daytime, stale daily counters must roll over.
            1_780_371_000,
            1_780_371_900,
            1_780_372_800,
            1_780_373_700,
            1_780_374_600,
            1_780_375_500,
            1_780_376_400,
            1_780_377_300,
            1_780_378_200,
            1_780_379_100,
            1_780_380_000,
            1_780_383_600,  # crossed local midnight into the next day.
            1_780_384_500,
            1_780_385_400,
            1_780_386_300,
            1_780_387_200,
            1_780_388_100,
            1_780_389_000,
            1_780_389_900,
            1_780_390_800,
            1_780_391_700,
            1_780_392_600,
        ]

        for index, epoch in enumerate(tick_epochs):
            if index == 4:
                write_json(
                    base_dir / "mrsMThatcher.control.json",
                    {
                        "disable_replies": True,
                        "pause_until_epoch": epoch + 3600,
                    },
                )
            elif index == 5:
                write_json(
                    base_dir / "bot_state.json",
                    {
                        **read_json(base_dir / "bot_state.json"),
                        "api_cooldown_until_epoch": epoch + 3600,
                        "api_cooldown_reason": "soak-test",
                    },
                )
                (base_dir / "mrsMThatcher.control.json").unlink(missing_ok=True)
            elif index == 6:
                state = read_json(base_dir / "bot_state.json")
                state["api_cooldown_until_epoch"] = 0
                state["api_cooldown_reason"] = ""
                write_json(base_dir / "bot_state.json", state)

            result = run_bot_command(
                base_dir,
                server,
                "--test-main-tick",
                extra_env={"MRS_FAKE_NOW_EPOCH": str(epoch)},
            )
            assert result.returncode == 0, result.stderr + result.stdout

        quote_result = run_bot_command(
            base_dir,
            server,
            "--test-post-quote",
            extra_env={"MRS_FAKE_NOW_EPOCH": str(1_780_377_000)},
        )
        assert quote_result.returncode == 0, quote_result.stderr + quote_result.stdout

        meme_result = run_bot_command(
            base_dir,
            server,
            "--test-post-meme",
            extra_env={"MRS_FAKE_NOW_EPOCH": str(1_780_379_200)},
        )
        assert meme_result.returncode == 0, meme_result.stderr + meme_result.stdout

        reply_targets = [
            post.get("reply", {}).get("in_reply_to_tweet_id")
            for post in server.posts
            if post.get("reply")
        ]
        main_posts = [
            post
            for post in server.posts
            if not post.get("reply") and not post.get("quote_tweet_id")
        ]

        assert len(reply_targets) == len(set(reply_targets))
        assert "100" in reply_targets
        assert "910" in reply_targets
        assert "101" in reply_targets or "911" in reply_targets or "300" in reply_targets
        assert server.path_counts.get("/2/tweets/search/recent", 0) > 0
        assert len(main_posts) == 2
        assert any(post.get("text") == "A test quote." for post in main_posts)
        assert any(post.get("text") == "meme" for post in main_posts)
        assert len(server.uploads) >= 2

        state = read_json(base_dir / "bot_state.json")
        assert "100" in state["replied_to_ids"]
        assert "910" in state["replied_to_quote_post_ids"]
        assert state["replied_to_ids"].count("100") == 1
        assert state["replied_to_quote_post_ids"].count("910") == 1
        assert state["daily_reply_date"] == "2026-06-02"
        assert state["daily_quote_reply_date"] == "2026-06-02"
        assert "stale-author" not in state["daily_replied_author_ids"]
        assert "stale-author" not in state["daily_replied_author_counts"]
        assert state["daily_reply_count"] <= 4
        assert state["daily_quote_reply_count"] <= 2
        assert state["last_reply_check_epoch"] <= tick_epochs[-1]
        assert state["last_quote_tweet_check_epoch"] <= tick_epochs[-1]
        assert state["last_reply_check_epoch"] >= tick_epochs[0]
        assert state["last_quote_tweet_check_epoch"] >= tick_epochs[0]
        assert state["next_meme_schedule_date"] >= "2026-06-02"
        assert state["posted_meme_filenames"] == ["001_test_meme.png"]
        assert state["next_reply_lane_priority"] in {"normal", "quote"}
        assert server.path_counts.get("/2/users/12345/mentions", 0) < len(tick_epochs)
        assert server.path_counts.get("/2/tweets/900/quote_tweets", 0) < len(tick_epochs)
    finally:
        server.stop()


def test_clock_rollback_normalizes_both_scheduler_epochs_and_polls(tmp_path: Path) -> None:
    server = FakeApiServer(
        {
            "tweets": {
                "900": {
                    "id": "900",
                    "text": "Original watched post.",
                    "author_id": "12345",
                    "conversation_id": "900",
                    "created_at": "2026-06-01T07:00:00Z",
                }
            },
            "quote_tweets": {"900": {}},
        }
    ).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "next_reply_lane_priority": "normal",
                "recent_own_post_ids": ["900"],
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 2_000_005_000,
                "last_quote_tweet_check_epoch": 2_000_005_000,
            },
            local_config={
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "REPLY_CHECK_EVERY_SECONDS": 900,
                "QUOTE_CHECK_EVERY_SECONDS": 900,
            },
        )

        result = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )

        assert result.returncode == 0, result.stderr + result.stdout
        assert server.path_counts.get("/2/users/12345/mentions") == 1
        assert server.path_counts.get("/2/tweets/900/quote_tweets") == 1
        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_check_epoch"] == 2_000_000_000
        assert state["last_quote_tweet_check_epoch"] == 2_000_000_000
    finally:
        server.stop()


def test_expired_runtime_pause_does_not_block_scheduler_tick(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            control={"pause_replies_until": 1_999_999_999},
            state={
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
            },
            local_config={
                "ENABLE_QUOTE_TWEET_CHECKS": False,
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "REPLY_CHECK_EVERY_SECONDS": 900,
            },
        )

        result = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )

        assert result.returncode == 0, result.stderr + result.stdout
        assert len(server.posts) == 1
        assert server.posts[0]["reply"]["in_reply_to_tweet_id"] == "100"
        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_check_epoch"] == 2_000_000_000
        assert "100" in state["replied_to_ids"]
    finally:
        server.stop()


def test_daily_reply_cap_survives_restarts_then_resets_after_midnight(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "daily_reply_date": "2026-06-01",
                "daily_reply_count": 1,
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
            },
            local_config={
                "ENABLE_QUOTE_TWEET_CHECKS": False,
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "MAX_AUTO_REPLIES_PER_DAY": 1,
                "REPLY_CHECK_EVERY_SECONDS": 900,
            },
        )

        before_midnight = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "1780353000"},
        )
        restart_before_midnight = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "1780353900"},
        )
        after_midnight = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "1780356600"},
        )

        assert before_midnight.returncode == 0, before_midnight.stderr + before_midnight.stdout
        assert restart_before_midnight.returncode == 0, restart_before_midnight.stderr + restart_before_midnight.stdout
        assert after_midnight.returncode == 0, after_midnight.stderr + after_midnight.stdout
        assert server.path_counts.get("/2/users/12345/mentions") == 1
        assert len(server.posts) == 1
        state = read_json(base_dir / "bot_state.json")
        assert state["daily_reply_date"] == "2026-06-02"
        assert state["daily_reply_count"] == 1
        assert state["daily_replied_author_ids"] == ["200"]
        assert "100" in state["replied_to_ids"]
    finally:
        server.stop()


def test_daily_quote_cap_survives_restarts_then_resets_after_midnight(tmp_path: Path) -> None:
    server = FakeApiServer(
        {
            "tweets": {
                "900": {
                    "id": "900",
                    "text": "Original watched post.",
                    "author_id": "12345",
                    "conversation_id": "900",
                    "created_at": "2026-06-01T07:00:00Z",
                }
            },
            "quote_tweets": {
                "900": {
                    "data": [
                        {
                            "id": "910",
                            "text": "@mrsMThatcher ? What will you tax next?",
                            "author_id": "310",
                            "conversation_id": "910",
                            "referenced_tweets": [{"type": "quoted", "id": "900"}],
                            "created_at": "2026-06-01T08:00:00Z",
                        }
                    ]
                }
            },
            "grok_replies": ["The list grows longer each time they need another pound."],
        }
    ).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "next_reply_lane_priority": "quote",
                "recent_own_post_ids": ["900"],
                "daily_reply_date": "2026-06-01",
                "daily_reply_count": 1,
                "daily_quote_reply_date": "2026-06-01",
                "daily_quote_reply_count": 1,
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
                "last_quote_tweet_check_epoch": 0,
            },
            local_config={
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "MAX_AUTO_REPLIES_PER_DAY": 2,
                "MAX_QUOTE_REPLIES_PER_DAY": 1,
                "REPLY_CHECK_EVERY_SECONDS": 900,
                "QUOTE_CHECK_EVERY_SECONDS": 900,
            },
        )

        before_midnight = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "1780353000"},
        )
        restart_before_midnight = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "1780353900"},
        )
        after_midnight = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "1780356600"},
        )

        assert before_midnight.returncode == 0, before_midnight.stderr + before_midnight.stdout
        assert restart_before_midnight.returncode == 0, restart_before_midnight.stderr + restart_before_midnight.stdout
        assert after_midnight.returncode == 0, after_midnight.stderr + after_midnight.stdout
        assert server.path_counts.get("/2/tweets/900/quote_tweets") == 1
        assert len(server.posts) == 1
        assert server.posts[0]["reply"]["in_reply_to_tweet_id"] == "910"
        state = read_json(base_dir / "bot_state.json")
        assert state["daily_quote_reply_date"] == "2026-06-02"
        assert state["daily_quote_reply_count"] == 1
        assert state["daily_reply_date"] == "2026-06-02"
        assert state["daily_reply_count"] == 1
        assert "910" in state["replied_to_quote_post_ids"]
    finally:
        server.stop()


def test_hot_post_full_rescan_after_restart_omits_since_id_without_duplicate(tmp_path: Path) -> None:
    server = FakeApiServer(
        {
            "search_recent": [
                {
                    "id": "302",
                    "text": "Already replied duplicate visible in full rescan",
                    "author_id": "402",
                    "conversation_id": "700",
                    "referenced_tweets": [{"type": "replied_to", "id": "700"}],
                    "created_at": "2026-06-30T12:02:00Z",
                }
            ],
            "grok_replies": ["This should not be used."],
        }
    ).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            watch_ids=["700"],
            state={
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
                "replied_to_ids": ["302"],
                "hot_post_reply_since_ids": {"700": "301"},
                "hot_post_reply_check_counts": {"700": 11},
            },
            local_config={
                "ENABLE_QUOTE_TWEET_CHECKS": False,
                "ENABLE_HOT_POST_REPLY_CHECKS": True,
                "REPLY_CHECK_EVERY_SECONDS": 900,
            },
        )

        result = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )

        assert result.returncode == 0, result.stderr + result.stdout
        recent_searches = [req for req in server.requests if req["path"] == "/2/tweets/search/recent"]
        assert len(recent_searches) == 1
        assert "since_id" not in recent_searches[0]["query"]
        assert len(server.posts) == 0
        assert len(server.xai_requests) == 0
        state = read_json(base_dir / "bot_state.json")
        assert state["hot_post_reply_check_counts"]["700"] == 12
        assert state["replied_to_ids"].count("302") == 1
    finally:
        server.stop()


def test_meme_schedule_after_midday_quote_survives_restart_and_posts_once(tmp_path: Path) -> None:
    server = FakeApiServer({"next_post_id": 950000}).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            meme=True,
            state={
                "next_reply_lane_priority": "quote",
                "next_meme_post_epoch": 0,
                "posted_meme_filenames": [],
            },
            local_config={
                "ENABLE_DAILY_MEME_POSTS": True,
                "MEME_POST_TEXT": "meme",
                "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS": 2400,
                "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS": 2400,
            },
        )

        quote_result = run_bot_command(
            base_dir,
            server,
            "--test-post-quote",
            extra_env={"MRS_FAKE_NOW_EPOCH": "1780313400"},
        )
        assert quote_result.returncode == 0, quote_result.stderr + quote_result.stdout
        state = read_json(base_dir / "bot_state.json")
        assert state["next_meme_schedule_mode"] == "after_first_quote_after_midday"
        assert state["next_meme_post_epoch"] == 1_780_315_800
        assert state["posted_meme_filenames"] == []

        before_meme_time = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "1780315200"},
        )
        assert before_meme_time.returncode == 0, before_meme_time.stderr + before_meme_time.stdout
        state = read_json(base_dir / "bot_state.json")
        assert state["next_meme_post_epoch"] == 1_780_315_800
        assert state["posted_meme_filenames"] == []

        first_meme = run_bot_command(
            base_dir,
            server,
            "--test-post-meme",
            extra_env={"MRS_FAKE_NOW_EPOCH": "1780315800"},
        )
        second_meme = run_bot_command(
            base_dir,
            server,
            "--test-post-meme",
            extra_env={"MRS_FAKE_NOW_EPOCH": "1780315900"},
        )

        assert first_meme.returncode == 0, first_meme.stderr + first_meme.stdout
        assert second_meme.returncode == 0, second_meme.stderr + second_meme.stdout
        main_posts = [
            post
            for post in server.posts
            if not post.get("reply") and not post.get("quote_tweet_id")
        ]
        assert [post.get("text") for post in main_posts].count("A test quote.") == 1
        assert [post.get("text") for post in main_posts].count("meme") == 1
        state = read_json(base_dir / "bot_state.json")
        assert state["posted_meme_filenames"] == ["001_test_meme.png"]
        assert state["next_meme_schedule_date"] == "2026-06-02"
    finally:
        server.stop()


def test_state_backup_rotation_during_scheduler_ticks_keeps_valid_json(tmp_path: Path) -> None:
    server = FakeApiServer({}).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
                "last_quote_tweet_check_epoch": 0,
            },
            local_config={
                "STATE_BACKUP_COUNT": 3,
                "ENABLE_QUOTE_TWEET_CHECKS": False,
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "REPLY_CHECK_EVERY_SECONDS": 1,
            },
        )

        for epoch in ["2000000000", "2000000001", "2000000002", "2000000003"]:
            result = run_bot_command(
                base_dir,
                server,
                "--test-main-tick",
                extra_env={"MRS_FAKE_NOW_EPOCH": epoch},
            )
            assert result.returncode == 0, result.stderr + result.stdout

        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_check_epoch"] == 2_000_000_003
        for suffix in [".bak1", ".bak2", ".bak3"]:
            backup_state = read_json(base_dir / f"bot_state.json{suffix}")
            assert isinstance(backup_state, dict)
            assert "last_reply_check_epoch" in backup_state
    finally:
        server.stop()


def test_startup_self_test_does_not_rotate_backups_when_scheduler_epochs_are_clean(tmp_path: Path) -> None:
    base_dir = prepare_base_dir(
        tmp_path,
        state={
            "last_reply_check_epoch": 1_999_999_000,
            "last_quote_tweet_check_epoch": 1_999_999_000,
        },
        local_config={"STATE_BACKUP_COUNT": 3, "MIN_SECONDS_BETWEEN_REPLIES": 1},
    )
    write_json(base_dir / "bot_state.json.bak1", {"sentinel": "bak1"})
    write_json(base_dir / "bot_state.json.bak2", {"sentinel": "bak2"})

    result = run_bot_with_env(
        base_dir,
        "--self-test",
        extra_env={
            "MRS_FAKE_NOW_EPOCH": "2000000000",
            "X_API_BASE_URL": "http://127.0.0.1:1",
            "X_UPLOAD_BASE_URL": "http://127.0.0.1:1",
            "XAI_API_BASE_URL": "http://127.0.0.1:1/v1",
            "X_BEARER_TOKEN": "dummy",
        },
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert read_json(base_dir / "bot_state.json.bak1") == {"sentinel": "bak1"}
    assert read_json(base_dir / "bot_state.json.bak2") == {"sentinel": "bak2"}


def test_launcher_matches_master_on_promotion_branch() -> None:
    result = subprocess.run(
        ["git", "diff", "--exit-code", "master", "--", "runMrsMThatcher2"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_malformed_quote_tweet_ids_are_skipped_without_crashing(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    scenario["quote_tweets"]["900"]["data"] = [
        {
            "id": "bad-quote-id",
            "text": "Malformed quote tweet.",
            "author_id": "311",
            "conversation_id": "bad-quote-id",
            "referenced_tweets": [{"type": "quoted", "id": "900"}],
            "created_at": "2026-06-30T09:59:00Z",
        },
        {
            "id": "911",
            "text": "Newer valid quote tweet.",
            "author_id": "312",
            "conversation_id": "911",
            "referenced_tweets": [{"type": "quoted", "id": "900"}],
            "created_at": "2026-06-30T10:01:00Z",
        },
        {
            "id": "910",
            "text": "Oldest valid quote tweet.",
            "author_id": "310",
            "conversation_id": "910",
            "referenced_tweets": [{"type": "quoted", "id": "900"}],
            "created_at": "2026-06-30T10:00:00Z",
        },
    ]
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
        assert server.posts[0]["reply"]["in_reply_to_tweet_id"] == "910"
        state = read_json(base_dir / "bot_state.json")
        assert "910" in state["replied_to_quote_post_ids"]
        assert "bad-quote-id" not in state["replied_to_quote_post_ids"]
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


def test_transient_parent_fetch_failure_does_not_advance_mention_watermark(tmp_path: Path) -> None:
    server = FakeApiServer(
        {
            "mentions": [
                {
                    "id": "100",
                    "text": "@mrsMThatcher what did you mean here?",
                    "author_id": "200",
                    "conversation_id": "100",
                    "referenced_tweets": [{"type": "replied_to", "id": "99"}],
                    "created_at": "2026-06-30T12:00:00Z",
                }
            ],
            "error_paths": {"/2/tweets/99": 503},
            "grok_replies": ["This should not be used."],
        }
    ).start()
    try:
        base_dir = prepare_base_dir(tmp_path, state={"last_seen_mention_id": None, "last_reply_epoch": 0})
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        assert len(server.posts) == 0
        assert server.path_counts.get("/v1/chat/completions", 0) == 0
        state = read_json(base_dir / "bot_state.json")
        assert state.get("last_seen_mention_id") is None
        assert state["x_error_epochs"]
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
        assert state["skipped_hot_reply_records"]["303"]["retryable"] is False

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
                "daily_replied_author_counts": {"200": 2},
                "daily_replied_author_ids": ["200"],
                "last_reply_epoch": 0,
            },
            local_config={"ENABLE_HOT_POST_REPLY_CHECKS": False},
        )
        result = run_bot_command(base_dir, server, extra_env={"MRS_FAKE_NOW_EPOCH": str(fake_now)})
        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        assert state["daily_quote_reply_date"] == "2026-07-01"
        assert state["daily_quote_reply_count"] == 1
        assert state["daily_replied_author_counts"] == {"310": 1}
        assert state["daily_replied_author_ids"] == ["310"]

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
                "MAX_MENTIONS_PER_CHECK": 0,
                "POST_SLEEP_MIN": 9000,
                "POST_SLEEP_MAX": 10,
                "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS": 1000,
                "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS": 60,
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
        assert "Ignoring invalid local config override MAX_MENTIONS_PER_CHECK" in result.stdout
        assert "Ignoring invalid local config timing range POST_SLEEP_MIN" in result.stdout
        assert "Ignoring invalid local config timing range MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS" in result.stdout
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


def test_per_author_cap_above_one_is_enforced(tmp_path: Path) -> None:
    server = FakeApiServer(
        {
            "mentions": [
                {
                    "id": "100",
                    "text": "@mrsMThatcher first",
                    "author_id": "240",
                    "conversation_id": "100",
                    "created_at": "2026-06-30T12:00:00Z",
                },
                {
                    "id": "101",
                    "text": "@mrsMThatcher second",
                    "author_id": "240",
                    "conversation_id": "101",
                    "created_at": "2026-06-30T12:01:00Z",
                },
                {
                    "id": "102",
                    "text": "@mrsMThatcher third",
                    "author_id": "240",
                    "conversation_id": "102",
                    "created_at": "2026-06-30T12:02:00Z",
                },
            ],
            "grok_replies": ["First reply.", "Second reply.", "Third reply should not be used."],
        }
    ).start()
    try:
        base_dir = prepare_base_dir(tmp_path, local_config={"MAX_REPLIES_PER_AUTHOR_PER_DAY": 2})

        first = run_cycle(base_dir, server)
        assert first.returncode == 0, first.stderr + first.stdout
        second = run_cycle(base_dir, server)
        assert second.returncode == 0, second.stderr + second.stdout
        third = run_cycle(base_dir, server)
        assert third.returncode == 0, third.stderr + third.stdout

        assert len(server.posts) == 2
        assert server.posts[0]["reply"]["in_reply_to_tweet_id"] == "100"
        assert server.posts[1]["reply"]["in_reply_to_tweet_id"] == "101"
        assert len(server.xai_requests) == 2
        state = read_json(base_dir / "bot_state.json")
        assert state["daily_replied_author_counts"]["240"] == 2
        assert state["daily_replied_author_ids"] == ["240"]
        assert state["last_seen_mention_id"] == "102"
    finally:
        server.stop()


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
    assert (base_dir / "lines_used.json").exists()
    assert (base_dir / "images_used.json").exists()


@pytest.mark.parametrize("fake_server", ["media_v2_fallback.json"], indirect=True)
def test_media_v2_failure_falls_back_to_v1_upload(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path)
    result = run_bot_command(base_dir, fake_server, "--test-post-quote")

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.path_counts["/2/media/upload"] == 1
    assert fake_server.path_counts["/1.1/media/upload.json"] == 1
    assert fake_server.posts[0]["media"]["media_ids"] == ["fake-media-v1-fallback"]


def test_quote_image_post_missing_created_post_id_fails_without_marking_assets_used(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIOS / "media_quote_post.json")
    scenario["tweet_post_responses"] = [{"status": 201, "body": {"data": {}}}]
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(tmp_path)
        result = run_bot_command(base_dir, server, "--test-post-quote")

        assert result.returncode == 1
        assert len(server.posts) == 1
        assert not (base_dir / "lines_used.json").exists()
        assert not (base_dir / "images_used.json").exists()
        state = read_json(base_dir / "bot_state.json")
        assert not state.get("last_main_post_id")
        assert state.get("recent_own_post_ids", []) == []
        assert len(state["x_error_epochs"]) == 1
    finally:
        server.stop()


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


def test_daily_meme_post_missing_created_post_id_fails_without_recording_or_rescheduling(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIOS / "meme_post.json")
    scenario["tweet_post_responses"] = [{"status": 201, "body": {"data": {}}}]
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            meme=True,
            local_config={"ENABLE_DAILY_MEME_POSTS": True, "MEME_POST_TEXT": "Test meme post"},
        )
        result = run_bot_command(base_dir, server, "--test-post-meme")

        assert result.returncode == 1
        assert len(server.posts) == 1
        state = read_json(base_dir / "bot_state.json")
        assert not state.get("last_main_post_id")
        assert state.get("posted_meme_filenames", []) == []
        assert not state.get("next_meme_post_epoch")
        assert len(state["x_error_epochs"]) == 1
    finally:
        server.stop()


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


def test_made_with_ai_network_failure_does_not_retry_ambiguous_post(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIOS / "normal_mention_reply.json")
    scenario["network_failures"] = {"/2/tweets": "closed"}
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={"last_reply_epoch": 0},
            local_config={"MARK_AI_REPLIES_AS_AI": True},
        )
        result = run_cycle(base_dir, server)

        assert result.returncode == 0, result.stderr + result.stdout
        assert server.path_counts["/2/tweets"] == 1
        assert server.posts == []
        state = read_json(base_dir / "bot_state.json")
        assert state.get("last_seen_mention_id") is None
        assert state["x_error_epochs"]
    finally:
        server.stop()


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


def test_test_mode_refuses_live_endpoints_with_explicit_ports(tmp_path: Path) -> None:
    base_dir = prepare_base_dir(tmp_path)
    result = run_bot_with_env(
        base_dir,
        extra_env={
            "X_API_BASE_URL": "https://api.x.com:443",
            "X_UPLOAD_BASE_URL": "https://upload.twitter.com:443",
            "XAI_API_BASE_URL": "https://api.x.ai:443/v1",
        },
    )

    assert result.returncode == 2
    assert "X_API_BASE_URL=https://api.x.com:443" in result.stdout
    assert "X_UPLOAD_BASE_URL=https://upload.twitter.com:443" in result.stdout
    assert "XAI_API_BASE_URL=https://api.x.ai:443/v1" in result.stdout


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


def test_invalid_request_timeout_env_falls_back_safely_in_test_mode(tmp_path: Path) -> None:
    base_dir = prepare_base_dir(tmp_path, local_config={"MIN_SECONDS_BETWEEN_REPLIES": 1})
    fake = "http://127.0.0.1:9"
    result = run_bot_with_env(
        base_dir,
        "--self-test",
        extra_env={
            "X_API_BASE_URL": fake,
            "X_UPLOAD_BASE_URL": fake,
            "XAI_API_BASE_URL": f"{fake}/v1",
            "MRS_REQUEST_TIMEOUT_SECONDS": "not-a-number",
        },
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Invalid MRS_REQUEST_TIMEOUT_SECONDS='not-a-number'; using default 60" in result.stdout


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


def test_quote_tweet_malformed_xai_success_records_xai_error(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    scenario["xai_success_body"] = {}
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={"next_reply_lane_priority": "quote", "recent_own_post_ids": ["900"], "last_reply_epoch": 0},
            local_config={"ENABLE_HOT_POST_REPLY_CHECKS": False},
        )
        result = run_cycle(base_dir, server)

        assert result.returncode == 0, result.stderr + result.stdout
        assert server.posts == []
        state = read_json(base_dir / "bot_state.json")
        assert len(state["xai_error_epochs"]) == 1
        assert state["x_error_epochs"] == []
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
        assert state["api_cooldown_until_epoch"] == 4102444860
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

    ops_base = prepare_base_dir(tmp_path / "digest-ops")
    (ops_base / "test.log").write_text(
        "\n".join(
            [
                "2026-07-02 10:00:00 ERROR    record_api_error:1122 - Entering API cooldown after 429 until 2099-12-31 00:01:00",
                "2026-07-02 10:01:00 ERROR    x_request:1254 - X API error 503: {\"detail\":\"Service Unavailable\"}",
                "2026-07-02 10:01:00 WARNING  print_rate_limit_headers:1194 - Rate Limit: 40000",
                "2026-07-02 10:01:00 WARNING  print_rate_limit_headers:1195 - Remaining: 40000",
                "2026-07-02 10:01:00 WARNING  record_api_error:1159 - Recorded x API error. status_code=503 errors_in_window=1/3 reset_epoch=1783050931 error=X API error 503: {\"detail\":\"Service Unavailable\"}",
                "2026-07-02 10:02:00 WARNING  in_api_cooldown:1125 - API cooldown active until 2099-12-31 00:01:00: too many x API errors in the last hour",
                "2026-07-02 10:03:00 DEBUG    save_state:994 - State being saved: {\"api_cooldown_until_epoch\": 1, \"api_cooldown_reason\": \"too many x API errors in the last hour\"}",
                "2026-07-02 10:00:01 INFO     load_used_set:829 - Migrated legacy pickle file /tmp/lines_used.pickle to JSON file /tmp/lines_used.json",
                "2026-07-02 10:00:02 INFO     load_used_set:817 - Normalized used-history JSON ordering in /tmp/images_used.json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    ops_digest = run_digest(ops_base)
    assert ops_digest.returncode == 0, ops_digest.stderr
    assert "API cooldown occurred" in ops_digest.stdout
    assert "API cooldowns entered" in ops_digest.stdout
    assert "API health" in ops_digest.stdout
    assert "mentions/hot-post" in ops_digest.stdout
    assert "not quota exhaustion" in ops_digest.stdout
    assert "Used-history migrations" in ops_digest.stdout
    assert "Used-history normalizations" in ops_digest.stdout

    stale_base = prepare_base_dir(tmp_path / "digest-stale-cooldown")
    (stale_base / "test.log").write_text(
        "\n".join(
            [
                "2026-07-03 12:00:00 DEBUG    save_state:994 - State being saved: {\"api_cooldown_until_epoch\": 1, \"api_cooldown_reason\": \"old cooldown\"}",
                "2026-07-03 12:01:00 INFO     maybe_reply_to_mentions:2931 - Starting mention reply check",
                "2026-07-03 12:01:00 INFO     maybe_reply_to_mentions:2975 - Skipping mention check: minimum interval between replies not reached",
                "2026-07-03 12:16:00 INFO     maybe_reply_to_mentions:2931 - Starting mention reply check",
                "2026-07-03 12:16:00 INFO     get_mentions:1679 - Fetching mentions. last_seen_mention_id=1 max_results=5",
                "2026-07-03 12:16:01 INFO     maybe_reply_to_mentions:3036 - Considering mention id=123 author_id=456 text='@MrsMThatcher @other'",
                "2026-07-03 12:16:01 INFO     maybe_reply_to_mentions:3078 - Skipping mention 123: spam/not worth replying",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    stale_digest = run_digest(stale_base)
    assert stale_digest.returncode == 0, stale_digest.stderr
    assert "no API cooldown" in stale_digest.stdout
    assert "API cooldown occurred" not in stale_digest.stdout
    assert "api_cooldown_until      = 1" in stale_digest.stdout
    assert "api_cooldown_reason     = old cooldown" in stale_digest.stdout
    assert "expired" in stale_digest.stdout
    assert "mention_fetch_attempts         = 1" in stale_digest.stdout
    assert "mention_checks_skipped_spacing = 1" in stale_digest.stdout
    assert "Mention direct skips" in stale_digest.stdout
    assert "@MrsMThatcher @other" in stale_digest.stdout

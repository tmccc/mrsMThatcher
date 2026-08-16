from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from remote_write_safety_protocol import (
    ACTIVATION_BASENAME as REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_BASENAME,
)
from tests.fake_api_server import FakeApiServer, load_scenario
from tests.helpers.protocol_activation import create_test_protocol_activation


ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "mrsMThatcher2.py"
DIGEST = ROOT / "mrs_log_digest.py"
SCENARIOS = ROOT / "tests" / "fixtures" / "scenarios"
PRODUCTION_BASE_DIR = Path("/disks/disk1/etc/mrsMThatcher")


def collapse_quote_whitespace(text: str) -> str:
    return " ".join(str(text or "").split())


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_minimal_asset_analysis(base_dir: Path) -> None:
    quote_text = "A test quote."
    quote_hash = hashlib.sha256(collapse_quote_whitespace(quote_text).encode("utf-8")).hexdigest()
    write_json(
        base_dir / "quote_analysis.json",
        {
            "analysis_kind": "quotes",
            "schema_version": 2,
            "source": {"source_sha256": hashlib.sha256((quote_text + "\n").encode("utf-8")).hexdigest()},
            "line_index": {"1": quote_hash},
            "items": {
                quote_hash: {
                    "quote_hash": quote_hash,
                    "text": quote_text,
                    "line_numbers": [1],
                    "analysis": {
                        "archive_image_preferences": {},
                        "primary_topics": [],
                        "secondary_topics": [],
                        "tone": [],
                        "visual_energy": "low",
                        "seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"},
                    },
                }
            },
        },
    )
    research_dir = (
        base_dir
        / "semantic_alignment_research"
        / "quote_research_full_001"
    )
    research_file = research_dir / "research_packets.json"
    research_file.parent.mkdir(parents=True, exist_ok=True)
    packet = {
        "quote_id": quote_hash,
        "quote_text": quote_text,
        "verification_status": "exact",
        "verified_text": quote_text,
        "text_variation_notes": "",
        "speaker": "Margaret Thatcher",
        "date": "1979-01-01",
        "source_event": "Synthetic offline test fixture",
        "stable_locator": "fixture:1",
        "historical_context": "Synthetic context.",
        "immediate_subject": "Synthetic subject.",
        "intended_argument": "Synthetic argument.",
        "literal_meaning": "Synthetic meaning.",
        "broader_principle": "Synthetic principle.",
        "mechanism": "Synthetic mechanism.",
        "claimed_consequence": "Synthetic consequence.",
        "entities": ["Margaret Thatcher"],
        "editorial_guidance": {
            "desired_first_impression": "Synthetic test fixture.",
            "historical_requirements": [],
            "must_be_visually_dominant": [],
            "must_not_dominate": [],
            "common_visual_mistakes": [],
        },
        "research_confidence": "high",
        "unresolved_questions": [],
        "sources": [
            {
                "title": "Synthetic offline source",
                "url": "https://example.invalid/synthetic",
                "source_type": "synthetic_test_fixture",
                "supports": ["Synthetic test quotation."],
            }
        ],
    }
    write_json(
        research_file,
        {
            "schema_version": 1,
            "items": {quote_hash: packet},
        },
    )
    write_json(
        research_dir / "corpus_manifest.json",
        {
            "record_count": 1,
            "records": [
                {
                    "quote_id": quote_hash,
                    "quote_text": quote_text,
                }
            ],
        },
    )
    final_unresolved = research_dir / "final_unresolved"
    final_unresolved.mkdir()
    write_json(
        final_unresolved / "final_research_status.json",
        {
            "completed_quotes": 1,
            "corpus_hash": sha256_bytes(research_file.read_bytes()),
            "total_manifest_quotes": 1,
            "unresolved_quote_ids": [],
            "unresolved_quotes": 0,
        },
    )
    runtime_manifest = (
        base_dir
        / "semantic_alignment_research"
        / "quote_attribution_cleanup_001"
        / "deployment_candidate"
        / "runtime_eligible_quote_manifest.json"
    )
    runtime_manifest.parent.mkdir(parents=True)
    write_json(
        runtime_manifest,
        {
            "eligibility_rule_version": (
                "canonical-principal-speaker-v2-reject-misattributed"
            ),
            "resolved_manifest_quote_ids": [quote_hash],
            "runtime_eligible_quote_count": 1,
            "runtime_eligible_quote_ids": [quote_hash],
            "runtime_quote_aliases": {},
            "schema_version": 1,
            "source_file_hashes": {
                "active_source": sha256_bytes(
                    (base_dir / "mrsMThatcher.txt").read_bytes()
                ),
                "completed_quote_research": sha256_bytes(
                    research_file.read_bytes()
                ),
            },
            "source_record_count": 1,
        },
    )
    write_json(base_dir / "quote_analysis_overrides.json", {"quote_overrides": {}})
    image_path = base_dir / "images" / "t01.jpg"
    image_hash = sha256_bytes(image_path.read_bytes())
    write_json(
        base_dir / "image_analysis.json",
        {
            "analysis_kind": "images",
            "schema_version": 3,
            "path_index": {"t01.jpg": image_hash},
            "items": {
                image_hash: {
                    "image_hash": image_hash,
                    "paths": ["t01.jpg"],
                    "analysis": {
                        "description": "A test image",
                        "pairing": {},
                        "themes": [],
                        "tone": [],
                        "visual_energy": "low",
                        "quality": {},
                        "seasonality": {"avoid_outside_season_or_occasion": False},
                    },
                }
            },
        },
    )


def read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def write_private_json(path: Path, data: dict) -> None:
    """Write a fixture for a production state file whose contract is mode 0600."""

    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def base_test_env() -> dict[str, str]:
    env = os.environ.copy()
    env["TZ"] = "Europe/London"
    return env


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
    create_test_protocol_activation(
        base_dir / REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_BASENAME
    )
    (base_dir / "mrsMThatcher.txt").write_text("A test quote.\n", encoding="utf-8")
    (base_dir / "images").mkdir()
    (base_dir / "images" / "t01.jpg").write_bytes(b"fake image bytes")
    (base_dir / "reply_factual_evidence.json").write_bytes(
        (ROOT / "reply_factual_evidence.json").read_bytes()
    )
    write_minimal_asset_analysis(base_dir)

    config = {
        "ENABLE_DAILY_MEME_POSTS": False,
        "MIN_SECONDS_BETWEEN_REPLIES": 1,
        "QUOTE_REPLY_DELAY_SECONDS": 0,
        "STATE_BACKUP_COUNT": 0,
        "MAX_MENTIONS_PER_CHECK": 10,
        "MAX_AUTO_REPLIES_PER_DAY": 48,
        "MAX_REPLIES_PER_AUTHOR_PER_DAY": 6,
        "ai_first_reply_strategy": {
            "enabled": True,
            "strategy_version": "ai-first-reply-v3",
            "proposer_model": "fixture-proposer",
            "reviewer_model": "fixture-reviewer",
            "evidence_model": "fixture-evidence",
            "research_corpus_path": str(ROOT / "semantic_alignment_research/quote_research_full_001"),
            "maximum_model_calls": 6,
            "proposer_timeout_seconds": 10,
            "evidence_timeout_seconds": 10,
            "reviewer_timeout_seconds": 10,
            "proposer_max_output_tokens": 900,
            "evidence_max_output_tokens": 1800,
            "reviewer_max_output_tokens": 900,
            "maximum_revisions": 1,
            "maximum_invalid_response_retries": 1,
            "maximum_claims": 6,
            "maximum_evidence_packets_per_claim": 6,
            "maximum_evidence_passages_per_claim": 24,
            "maximum_reply_sentences": 2,
            "fail_closed": True,
        },
    }
    if local_config:
        config.update(local_config)
    write_json(base_dir / "mrsMThatcher.local.json", config)

    write_json(base_dir / "bot_state.json", state or {})
    write_json(base_dir / "lines_used.json", [])
    write_json(base_dir / "images_used.json", [])
    write_private_json(
        base_dir / "historical_context_reply_history.json",
        {"schema_version": 1, "items": {}},
    )
    write_private_json(
        base_dir / "historical_context_reply_outbox.json",
        {
            "schema_version": 1,
            "retry_policy": {
                "max_attempts": 5,
                "base_backoff_seconds": 60,
                "max_backoff_seconds": 3_600,
            },
            "retired_parent_post_id_floor": "0",
            "obligations": {},
        },
    )
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
    unset_x_upload_base_url: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = base_test_env()
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
    if unset_x_upload_base_url:
        env.pop("X_UPLOAD_BASE_URL", None)
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
    env = base_test_env()
    # Unlike ``run_bot_command``, this helper deliberately exercises the
    # application's endpoint defaults when an endpoint is not supplied by the
    # caller.  The collection-time pytest safety bootstrap installs dead
    # loopback endpoints in the parent process, so remove those inherited
    # defaults here before applying the test-specific environment.
    env.pop("X_API_BASE_URL", None)
    env.pop("X_UPLOAD_BASE_URL", None)
    env.pop("XAI_API_BASE_URL", None)
    env.pop("MRS_ALLOW_LIVE_ENDPOINTS_IN_TEST", None)
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


def run_digest(
    base_dir: Path,
    *,
    state_file: Path | None = None,
    until: str | None = None,
) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        str(DIGEST),
        "--project-dir",
        str(base_dir),
        "--glob",
        "test.log",
    ]
    if state_file is None:
        args.append("--no-state")
    else:
        args.extend(["--state-file", str(state_file)])
    if until is not None:
        args.extend(["--until", until])
    args.append(str(base_dir / "test.log"))
    return subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def write_digest_log(base_dir: Path, lines: list[str]) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / "test.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_digest_reports_historical_context_reply_outcomes(tmp_path: Path) -> None:
    base_dir = prepare_base_dir(tmp_path / "digest-historical-context")
    quote_a = "a" * 64
    quote_b = "b" * 64
    write_digest_log(
        base_dir,
        [
            f'2026-07-14 21:01:40 INFO     log_event:330 - EVENT {{"event":"historical_context_reply","status":"completed","parent_post_id":"2074000000000000001","quote_id":"{quote_a}","character_count":512}}',
            f'2026-07-14 21:02:40 INFO     log_event:330 - EVENT {{"event":"historical_context_reply","status":"already_completed","parent_post_id":"2074000000000000001","quote_id":"{quote_a}","character_count":512}}',
            f'2026-07-14 23:01:40 INFO     log_event:330 - EVENT {{"event":"historical_context_reply","status":"failed","parent_post_id":"2074000000000000002","quote_id":"{quote_b}","character_count":431}}',
            f'2026-07-14 23:02:40 INFO     log_event:330 - EVENT {{"event":"historical_context_reply","status":"skipped_no_completed_packet","parent_post_id":"2074000000000000003","quote_id":"{quote_b}","character_count":0}}',
        ],
    )

    result = run_digest(base_dir)

    assert result.returncode == 0, result.stderr
    assert "1 historical-context reply completed" in result.stdout
    assert "Historical context replies" in result.stdout
    assert "already_completed" in result.stdout
    assert "skipped_no_completed_packet" in result.stdout
    assert "2074000000000000002" in result.stdout


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


def fake_server_post_replies(server: FakeApiServer) -> list[str]:
    return [
        str(post.get("reply", {}).get("in_reply_to_tweet_id"))
        for post in server.posts
        if post.get("reply", {}).get("in_reply_to_tweet_id")
    ]


@pytest.fixture
def fake_server(request):
    scenario = load_scenario(SCENARIOS / request.param)
    server = FakeApiServer(scenario).start()
    try:
        yield server
    finally:
        server.stop()


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
        env = base_test_env()
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


@pytest.mark.parametrize("fake_server", ["normal_mention_reply.json"], indirect=True)
def test_missing_reply_evidence_fails_before_model_or_post(
    tmp_path: Path,
    fake_server: FakeApiServer,
) -> None:
    base_dir = prepare_base_dir(tmp_path)
    config = read_json(base_dir / "mrsMThatcher.local.json")
    config["ai_first_reply_strategy"]["research_corpus_path"] = str(
        base_dir / "missing-research-corpus"
    )
    write_json(base_dir / "mrsMThatcher.local.json", config)

    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.xai_requests == []
    assert fake_server.uploads == []
    assert fake_server.posts == []
    assert "reply_evidence_unavailable" in result.stdout
    state = read_json(base_dir / "bot_state.json")
    assert state.get("xai_error_epochs", []) == []


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


@pytest.mark.parametrize(
    ("first_priority", "expected_targets"),
    [
        ("normal", ["100", "910"]),
        ("quote", ["910", "100"]),
    ],
)
def test_global_1800_reply_spacing_blocks_cross_lane_until_boundary(
    tmp_path: Path,
    first_priority: str,
    expected_targets: list[str],
) -> None:
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
        "A point is useful only when it survives contact with reality. This one rather does.",
    ]
    server = FakeApiServer(scenario).start()
    try:
        start = 2_000_000_000
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "next_reply_lane_priority": first_priority,
                "recent_own_post_ids": ["900"],
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 0,
                "last_quote_tweet_check_epoch": 0,
            },
            local_config={
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "MIN_SECONDS_BETWEEN_REPLIES": 1800,
                "REPLY_CHECK_EVERY_SECONDS": 1,
                "QUOTE_CHECK_EVERY_SECONDS": 1,
            },
        )

        first = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": str(start)},
        )
        assert first.returncode == 0, first.stderr + first.stdout
        assert [post["reply"]["in_reply_to_tweet_id"] for post in server.posts] == expected_targets[:1]
        assert read_json(base_dir / "bot_state.json")["last_reply_epoch"] == start
        requests_after_first = len(server.requests)

        blocked = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": str(start + 1799)},
        )
        assert blocked.returncode == 0, blocked.stderr + blocked.stdout
        assert len(server.requests) == requests_after_first
        assert [post["reply"]["in_reply_to_tweet_id"] for post in server.posts] == expected_targets[:1]
        assert read_json(base_dir / "bot_state.json")["last_reply_epoch"] == start

        boundary = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": str(start + 1800)},
        )
        assert boundary.returncode == 0, boundary.stderr + boundary.stdout
        assert [post["reply"]["in_reply_to_tweet_id"] for post in server.posts] == expected_targets
        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_epoch"] == start + 1800
        assert state["daily_reply_count"] == 2
        assert state["daily_quote_reply_count"] == 1
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
        assert expected_state_key == "last_seen_mention_id"
        assert state["last_seen_mention_id"] == expected_id
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
        assert state["last_reply_epoch"] == 0
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
                "MIN_SECONDS_BETWEEN_REPLIES": 1,
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
                "hot_post_reply_since_ids": {"700": "301", "old-watch": "999"},
                "hot_post_reply_check_counts": {"700": 11, "old-watch": 102},
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
        assert "old-watch" not in state["hot_post_reply_since_ids"]
        assert "old-watch" not in state["hot_post_reply_check_counts"]
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


def test_self_test_uses_separate_log_when_log_file_not_overridden(tmp_path: Path) -> None:
    base_dir = prepare_base_dir(tmp_path)
    env = base_test_env()
    env.pop("MRS_LOG_FILE", None)
    env.update(
        {
            "MRS_TEST_MODE": "1",
            "MRS_BASE_DIR": str(base_dir),
            "X_API_BASE_URL": "http://127.0.0.1:1",
            "X_UPLOAD_BASE_URL": "http://127.0.0.1:1",
            "XAI_API_BASE_URL": "http://127.0.0.1:1/v1",
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

    result = subprocess.run(
        [sys.executable, str(BOT), "--self-test"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert (base_dir / "mrsMThatcher.selftest.log").exists()
    assert not (base_dir / "mrsMThatcher.log").exists()


def test_launcher_restarts_after_child_exits_nonzero(tmp_path: Path) -> None:
    work_dir = tmp_path / "launcher-work"
    work_dir.mkdir()
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    count_file = tmp_path / "count.txt"
    fake_bot = tmp_path / "fake-bot.sh"
    fake_bot.write_text(
        "#!/bin/bash\n"
        "count=0\n"
        "if [[ -f \"$MRS_FAKE_COUNT_FILE\" ]]; then count=$(cat \"$MRS_FAKE_COUNT_FILE\"); fi\n"
        "count=$((count + 1))\n"
        "echo \"$count\" > \"$MRS_FAKE_COUNT_FILE\"\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_bot.chmod(0o755)

    env = base_test_env()
    env.update(
        {
            "MRS_TEST_MODE": "1",
            "MRS_WORK_DIR": str(work_dir),
            "MRS_ENV_FILE": str(env_file),
            "MRS_BOT_SCRIPT": str(fake_bot),
            "MRS_RESTART_SLEEP_SECONDS": "0.05",
            "MRS_FAST_FAILURE_WINDOW_SECONDS": "10",
            "MRS_MAX_CONSECUTIVE_FAST_FAILURES": "50",
            "MRS_FAKE_COUNT_FILE": str(count_file),
        }
    )
    proc = subprocess.Popen(
        [str(ROOT / "runMrsMThatcher2")],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.time() + 3
        while time.time() < deadline:
            if count_file.exists() and int(count_file.read_text(encoding="utf-8").strip() or "0") >= 2:
                break
            time.sleep(0.05)
        assert count_file.exists()
        assert int(count_file.read_text(encoding="utf-8").strip()) >= 2
        assert proc.poll() is None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_launcher_surfaces_child_output_and_exits_after_fast_failure_limit(
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "launcher-work"
    work_dir.mkdir()
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    count_file = tmp_path / "count.txt"
    fake_bot = tmp_path / "fake-bot.sh"
    fake_bot.write_text(
        "#!/bin/bash\n"
        "count=0\n"
        "if [[ -f \"$MRS_FAKE_COUNT_FILE\" ]]; then count=$(cat \"$MRS_FAKE_COUNT_FILE\"); fi\n"
        "count=$((count + 1))\n"
        "echo \"$count\" > \"$MRS_FAKE_COUNT_FILE\"\n"
        "echo \"child stdout $count\"\n"
        "echo \"child stderr $count\" >&2\n"
        "exit 7\n",
        encoding="utf-8",
    )
    fake_bot.chmod(0o755)

    env = base_test_env()
    env.update(
        {
            "MRS_TEST_MODE": "1",
            "MRS_WORK_DIR": str(work_dir),
            "MRS_ENV_FILE": str(env_file),
            "MRS_BOT_SCRIPT": str(fake_bot),
            "MRS_RESTART_SLEEP_SECONDS": "0.01",
            "MRS_FAST_FAILURE_WINDOW_SECONDS": "10",
            "MRS_MAX_CONSECUTIVE_FAST_FAILURES": "3",
            "MRS_FAKE_COUNT_FILE": str(count_file),
        }
    )

    result = subprocess.run(
        [str(ROOT / "runMrsMThatcher2")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 7
    assert count_file.read_text(encoding="utf-8").strip() == "3"
    assert "child stdout 1" in result.stdout
    assert "child stdout 3" in result.stdout
    assert "child stderr 1" in result.stderr
    assert "child stderr 3" in result.stderr
    assert "fast failure limit 3/3 reached" in result.stderr
    assert "exiting launcher for systemd restart" in result.stderr


def test_launcher_treats_repeated_fast_clean_exits_as_unhealthy(
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "launcher-work"
    work_dir.mkdir()
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    fake_bot = tmp_path / "fake-bot.sh"
    fake_bot.write_text(
        "#!/bin/bash\n"
        "echo \"unexpected clean exit\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_bot.chmod(0o755)
    env = base_test_env()
    env.update(
        {
            "MRS_TEST_MODE": "1",
            "MRS_WORK_DIR": str(work_dir),
            "MRS_ENV_FILE": str(env_file),
            "MRS_BOT_SCRIPT": str(fake_bot),
            "MRS_RESTART_SLEEP_SECONDS": "0.01",
            "MRS_FAST_FAILURE_WINDOW_SECONDS": "10",
            "MRS_MAX_CONSECUTIVE_FAST_FAILURES": "2",
        }
    )

    result = subprocess.run(
        [str(ROOT / "runMrsMThatcher2")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout.count("unexpected clean exit") == 2
    assert "fast failure limit 2/2 reached" in result.stderr


def test_launcher_setup_failure_exits_before_starting_child(tmp_path: Path) -> None:
    count_file = tmp_path / "count.txt"
    fake_bot = tmp_path / "fake-bot.sh"
    fake_bot.write_text(
        "#!/bin/bash\n"
        "echo started >> \"$MRS_FAKE_COUNT_FILE\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_bot.chmod(0o755)

    env = base_test_env()
    env.update(
        {
            "MRS_TEST_MODE": "1",
            "MRS_WORK_DIR": str(tmp_path / "does-not-exist"),
            "MRS_ENV_FILE": str(tmp_path / "missing.env"),
            "MRS_BOT_SCRIPT": str(fake_bot),
            "MRS_RESTART_SLEEP_SECONDS": "0.05",
            "MRS_FAKE_COUNT_FILE": str(count_file),
        }
    )
    result = subprocess.run(
        [str(ROOT / "runMrsMThatcher2")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert result.returncode != 0
    assert not count_file.exists()


@pytest.mark.parametrize("override_name", ["MRS_WORK_DIR", "MRS_ENV_FILE", "MRS_BOT_SCRIPT"])
@pytest.mark.parametrize("override_source", ["inherited", "env_file"])
def test_launcher_rejects_production_path_and_script_overrides(
    tmp_path: Path,
    override_name: str,
    override_source: str,
) -> None:
    production_dir = tmp_path / "production"
    production_dir.mkdir()
    launcher = tmp_path / "runMrsMThatcher2"
    launcher.write_text(
        (ROOT / "runMrsMThatcher2").read_text(encoding="utf-8").replace(
            "/disks/disk1/etc/mrsMThatcher", str(production_dir)
        ),
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    override_value = tmp_path / "override"
    env_file = production_dir / "mrsMThatcher.env"
    env_file.write_text(
        (
            f"{override_name}={override_value}\n"
            "MRS_RESTART_SLEEP_SECONDS=0\n"
            "MRS_FAST_FAILURE_WINDOW_SECONDS=10\n"
            "MRS_MAX_CONSECUTIVE_FAST_FAILURES=1\n"
            if override_source == "env_file"
            else ""
        ),
        encoding="utf-8",
    )
    env = base_test_env()
    env.pop("MRS_TEST_MODE", None)
    for name in ("MRS_WORK_DIR", "MRS_ENV_FILE", "MRS_BOT_SCRIPT"):
        env.pop(name, None)
    if override_source == "inherited":
        env[override_name] = str(override_value)

    result = subprocess.run(
        [str(launcher)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 2
    assert f"{override_name} is test-only and is refused unless MRS_TEST_MODE=1" in result.stderr


def test_launcher_rejects_env_file_work_dir_reassignment(tmp_path: Path) -> None:
    work_dir = tmp_path / "launcher-work"
    work_dir.mkdir()
    count_file = tmp_path / "count.txt"
    fake_bot = tmp_path / "fake-bot.sh"
    fake_bot.write_text(
        "#!/bin/bash\n"
        "echo started >> \"$MRS_FAKE_COUNT_FILE\"\n",
        encoding="utf-8",
    )
    fake_bot.chmod(0o755)
    env_file = tmp_path / "launcher.env"
    env_file.write_text(f"WORK_DIR={tmp_path / 'redirected'}\n", encoding="utf-8")
    env = base_test_env()
    env.update(
        {
            "MRS_TEST_MODE": "1",
            "MRS_WORK_DIR": str(work_dir),
            "MRS_ENV_FILE": str(env_file),
            "MRS_BOT_SCRIPT": str(fake_bot),
            "MRS_FAKE_COUNT_FILE": str(count_file),
        }
    )

    result = subprocess.run(
        [str(ROOT / "runMrsMThatcher2")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert result.returncode != 0
    assert "WORK_DIR: readonly variable" in result.stderr
    assert not count_file.exists()


def test_launcher_env_file_test_mode_cannot_authorise_override(tmp_path: Path) -> None:
    production_dir = tmp_path / "production"
    production_dir.mkdir()
    launcher = tmp_path / "runMrsMThatcher2"
    launcher.write_text(
        (ROOT / "runMrsMThatcher2").read_text(encoding="utf-8").replace(
            "/disks/disk1/etc/mrsMThatcher", str(production_dir)
        ),
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    count_file = tmp_path / "count.txt"
    fake_bot = tmp_path / "fake-bot.sh"
    fake_bot.write_text(
        "#!/bin/bash\n"
        "echo started >> \"$MRS_FAKE_COUNT_FILE\"\n",
        encoding="utf-8",
    )
    fake_bot.chmod(0o755)
    env_file = production_dir / "mrsMThatcher.env"
    env_file.write_text(
        f"MRS_TEST_MODE=1\nMRS_BOT_SCRIPT={fake_bot}\n",
        encoding="utf-8",
    )
    env = base_test_env()
    env.pop("MRS_TEST_MODE", None)
    for name in ("MRS_WORK_DIR", "MRS_ENV_FILE", "MRS_BOT_SCRIPT"):
        env.pop(name, None)
    env["MRS_FAKE_COUNT_FILE"] = str(count_file)

    result = subprocess.run(
        [str(launcher)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 2
    assert "the environment file cannot authorise test hooks" in result.stderr
    assert not count_file.exists()


def test_launcher_honours_test_bot_script_from_env_file(tmp_path: Path) -> None:
    work_dir = tmp_path / "launcher-work"
    work_dir.mkdir()
    count_file = tmp_path / "count.txt"
    fake_bot = tmp_path / "fake-bot.sh"
    fake_bot.write_text(
        "#!/bin/bash\n"
        "echo started >> \"$MRS_FAKE_COUNT_FILE\"\n"
        "exit 9\n",
        encoding="utf-8",
    )
    fake_bot.chmod(0o755)
    env_file = tmp_path / "launcher.env"
    env_file.write_text(
        "\n".join(
            [
                f"MRS_BOT_SCRIPT={fake_bot}",
                "MRS_RESTART_SLEEP_SECONDS=0",
                "MRS_FAST_FAILURE_WINDOW_SECONDS=10",
                "MRS_MAX_CONSECUTIVE_FAST_FAILURES=1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env = base_test_env()
    env.pop("MRS_BOT_SCRIPT", None)
    env.update(
        {
            "MRS_TEST_MODE": "1",
            "MRS_WORK_DIR": str(work_dir),
            "MRS_ENV_FILE": str(env_file),
            "MRS_FAKE_COUNT_FILE": str(count_file),
        }
    )

    result = subprocess.run(
        [str(ROOT / "runMrsMThatcher2")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 9
    assert count_file.read_text(encoding="utf-8").splitlines() == ["started"]
    assert "fast failure limit 1/1 reached" in result.stderr


@pytest.mark.parametrize(
    ("env_line", "expected_error"),
    [
        (
            "MRS_FAST_FAILURE_WINDOW_SECONDS=0\n",
            "MRS_FAST_FAILURE_WINDOW_SECONDS must be a positive integer",
        ),
        (
            "MRS_MAX_CONSECUTIVE_FAST_FAILURES=not-an-integer\n",
            "MRS_MAX_CONSECUTIVE_FAST_FAILURES must be a positive integer",
        ),
        (
            "MRS_RESTART_SLEEP_SECONDS=-1\n",
            "MRS_RESTART_SLEEP_SECONDS must be a non-negative number",
        ),
    ],
)
def test_launcher_validates_effective_settings_loaded_from_env_file(
    tmp_path: Path,
    env_line: str,
    expected_error: str,
) -> None:
    work_dir = tmp_path / "launcher-work"
    work_dir.mkdir()
    env_file = tmp_path / "launcher.env"
    env_file.write_text(env_line, encoding="utf-8")
    count_file = tmp_path / "count.txt"
    fake_bot = tmp_path / "fake-bot.sh"
    fake_bot.write_text(
        "#!/bin/bash\n"
        "echo started >> \"$MRS_FAKE_COUNT_FILE\"\n",
        encoding="utf-8",
    )
    fake_bot.chmod(0o755)

    env = base_test_env()
    env.update(
        {
            "MRS_TEST_MODE": "1",
            "MRS_WORK_DIR": str(work_dir),
            "MRS_ENV_FILE": str(env_file),
            "MRS_BOT_SCRIPT": str(fake_bot),
            "MRS_FAKE_COUNT_FILE": str(count_file),
        }
    )
    for name in (
        "MRS_RESTART_SLEEP_SECONDS",
        "MRS_FAST_FAILURE_WINDOW_SECONDS",
        "MRS_MAX_CONSECUTIVE_FAST_FAILURES",
    ):
        env.pop(name, None)

    result = subprocess.run(
        [str(ROOT / "runMrsMThatcher2")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 2
    assert expected_error in result.stderr
    assert not count_file.exists()


def test_launcher_has_valid_shell_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(ROOT / "runMrsMThatcher2")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 0, result.stderr


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


def test_missing_parent_404_does_not_block_later_mentions_or_trip_breaker(tmp_path: Path) -> None:
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
                },
                {
                    "id": "101",
                    "text": "@mrsMThatcher a later mention should still be considered",
                    "author_id": "201",
                    "conversation_id": "101",
                    "created_at": "2026-06-30T12:01:00Z",
                },
            ],
            "error_paths": {"/2/tweets/99": 404},
            "grok_replies": ["Answering without the missing parent is fine."],
        }
    ).start()
    try:
        base_dir = prepare_base_dir(tmp_path, local_config={"MAX_REPLIES_PER_AUTHOR_PER_DAY": 2})
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout

        state = read_json(base_dir / "bot_state.json")
        assert fake_server_post_replies(server) == ["100"]
        assert state["x_error_epochs"] == []
        assert state["api_cooldown_until_epoch"] == 0
        assert state["last_seen_mention_id"] == "100"
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
                "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
                "referenced_tweets": [{"type": "replied_to", "id": "700"}],
                "created_at": "2026-06-30T12:00:00Z",
            }
        ],
        "grok_replies": ["A sensible answer to the hot post."],
    }
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(tmp_path, watch_ids=["700"])

        first = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"})
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
        second = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000002"})
        assert second.returncode == 0, second.stderr + second.stdout
        state = read_json(base_dir / "bot_state.json")
        assert state["hot_post_reply_since_ids"]["700"] == "301"

        scenario["search_recent"] = [
            {
                "id": "302",
                "text": "A new reply after watermark",
                "author_id": "402",
                "conversation_id": "700",
                "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
                "referenced_tweets": [{"type": "replied_to", "id": "700"}],
                "created_at": "2026-06-30T12:02:00Z",
            }
        ]
        scenario["grok_replies"] = ["Reply after watermark."]
        third = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000004"})
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
                "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
                "referenced_tweets": [{"type": "replied_to", "id": "700"}],
                "created_at": "2026-06-30T12:03:00Z",
            }
        ]
        scenario["grok_replies"] = ["SKIP"]
        fourth = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000006"})
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
                "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
                "referenced_tweets": [{"type": "replied_to", "id": "700"}],
                "created_at": "2026-06-30T12:02:00Z",
            }
        ]
        before_posts = len(server.posts)
        before_xai = len(server.xai_requests)
        fifth = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000008"})
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
        assert len(server.xai_requests) == 3
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


def test_state_recovery_skips_semantically_invalid_primary_state(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(tmp_path, local_config={"STATE_BACKUP_COUNT": 2})
        write_json(
            base_dir / "bot_state.json",
            {
                "daily_reply_count": "banana",
                "tweet_cache": [],
                "replied_to_ids": [],
            },
        )
        write_json(
            base_dir / "bot_state.json.bak1",
            {
                "replied_to_ids": ["from-valid-backup"],
                "last_reply_epoch": 0,
                "hot_post_reply_since_ids": {"700": "555"},
                "skipped_hot_reply_records": {"skip": {"reason": "test", "retryable": False, "skipped_epoch": 1}},
            },
        )

        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        assert "from-valid-backup" in state["replied_to_ids"]
        assert state["hot_post_reply_since_ids"]["700"] == "555"
        assert "Recovered state from backup" in result.stdout
        assert "semantically" not in result.stderr.lower()
    finally:
        server.stop()


def test_state_recovery_normalises_numeric_strings_without_crashing(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "daily_reply_date": datetime.now().strftime("%Y-%m-%d"),
                "daily_reply_count": "1",
                "last_reply_epoch": "0",
                "x_error_epochs": ["1", "2"],
            },
        )

        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        assert isinstance(state["daily_reply_count"], int)
        assert state["daily_reply_count"] >= 2
        assert state["x_error_epochs"] == [1, 2]
    finally:
        server.stop()


def test_state_recovery_rejects_nested_malformed_cache_before_backup(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(tmp_path, local_config={"STATE_BACKUP_COUNT": 1})
        write_json(base_dir / "bot_state.json", {"tweet_cache": {"123": []}, "replied_to_ids": []})
        write_json(base_dir / "bot_state.json.bak1", {"replied_to_ids": ["from-bak1"], "last_reply_epoch": 0})

        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        assert "from-bak1" in state["replied_to_ids"]
        assert "Recovered state from backup" in result.stdout
    finally:
        server.stop()


def test_existing_state_all_unusable_fails_closed(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(tmp_path, local_config={"STATE_BACKUP_COUNT": 2})
        (base_dir / "bot_state.json").write_text("{bad", encoding="utf-8")
        (base_dir / "bot_state.json.bak1").write_text("{also bad", encoding="utf-8")
        write_json(base_dir / "bot_state.json.bak2", {"tweet_cache": {"123": []}})

        result = run_cycle(base_dir, server)
        assert result.returncode != 0
        assert server.posts == []
        assert "refusing to start with empty state" in (result.stdout + result.stderr)
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
        assert missing.returncode != 0
        assert "refusing to start with empty state" in (missing.stdout + missing.stderr)
    finally:
        server.stop()


def test_missing_and_malformed_state_fail_safely(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(tmp_path)
        (base_dir / "bot_state.json").unlink()
        missing = run_cycle(base_dir, server)
        assert missing.returncode != 0
        assert "Required durable production state/history is missing" in (missing.stdout + missing.stderr)

        (base_dir / "bot_state.json").write_text("{not json", encoding="utf-8")
        server.scenario.update(load_scenario(SCENARIOS / "normal_mention_reply.json"))
        server.scenario["mentions"][0]["id"] = "101"
        server.scenario["grok_replies"] = ["Recovered from malformed state."]
        malformed = run_cycle(base_dir, server)
        assert malformed.returncode != 0
        assert "refusing to start with empty state" in (malformed.stdout + malformed.stderr)
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
    fake_now_dt = datetime(
        2026, 7, 1, 23, 30, tzinfo=ZoneInfo("Europe/London")
    )
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
        assert malformed_server.posts == []
        assert "failing safe" in result.stdout
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
        state_before = (base_dir / "bot_state.json").read_bytes()
        result = run_cycle(base_dir, server)
        assert result.returncode != 0
        assert server.posts == []
        assert "Unsupported local config key" in result.stdout
        assert "refusing to ignore a possible safety-setting typo" in result.stdout
        assert "Ignoring invalid local config override ENABLE_AUTO_REPLIES" in result.stdout
        assert "Ignoring invalid local config override MIN_SECONDS_BETWEEN_REPLIES" in result.stdout
        assert "Ignoring invalid local config override MAX_MENTIONS_PER_CHECK" in result.stdout
        assert "LocalConfigError: Unsupported local config key" in result.stderr
        assert (base_dir / "bot_state.json").read_bytes() == state_before
    finally:
        server.stop()


def test_runtime_config_validation_rejects_unsafe_domain_values(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            local_config={
                "MAX_MENTIONS_PER_CHECK": 1,
                "QUOTE_LOOKUP_API_MAX_RESULTS": 9,
                "HOT_POST_REPLY_SEARCH_API_MAX_RESULTS": 9,
                "REPLY_CHECK_EVERY_SECONDS": 0,
                "MIN_SECONDS_BETWEEN_REPLIES": 0,
                "MEME_FALLBACK_HOUR": 99,
                "MEME_FALLBACK_MINUTE": 99,
            },
        )
        result = run_cycle(base_dir, server)
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "Invalid local config" in combined
        assert "MAX_MENTIONS_PER_CHECK must be between 5 and 100" in combined
        assert "QUOTE_LOOKUP_API_MAX_RESULTS must be between 10 and 100" in combined
        assert "HOT_POST_REPLY_SEARCH_API_MAX_RESULTS must be between 10 and 100" in combined
        assert "REPLY_CHECK_EVERY_SECONDS must be positive" in combined
        assert "MIN_SECONDS_BETWEEN_REPLIES must be positive" in combined
        assert "MEME_FALLBACK_HOUR must be between 0 and 23" in combined
        assert "MEME_FALLBACK_MINUTE must be between 0 and 59" in combined
    finally:
        server.stop()


def test_instance_lock_refuses_second_process_on_same_state_dir(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(tmp_path)
        lock_path = base_dir / "mrsMThatcher.lock"
        with open(lock_path, "w", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = run_cycle(base_dir, server)

        assert result.returncode == 2
        assert "already holds lock" in result.stdout
        assert server.posts == []
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
        assert len(server.xai_requests) == 3
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


def test_link_only_quote_tweet_skips_without_calling_grok(tmp_path: Path) -> None:
    server = FakeApiServer(
        {
            "tweets": {"900": {"id": "900", "author_id": "12345", "text": "Original post"}},
            "quote_tweets": {
                "900": {
                    "data": [
                        {
                            "id": "910",
                            "text": "https://t.co/abc https://t.co/def",
                            "author_id": "310",
                            "conversation_id": "910",
                            "referenced_tweets": [{"type": "quoted", "id": "900"}],
                            "created_at": "2026-06-30T10:01:00Z",
                        }
                    ],
                    "includes": {
                        "users": [
                            {
                                "id": "310",
                                "name": "Normal User",
                                "username": "normal",
                                "description": "Ordinary profile",
                            }
                        ]
                    },
                }
            },
        }
    ).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={"next_reply_lane_priority": "quote", "recent_own_post_ids": ["900"], "last_reply_epoch": 0},
            local_config={"ENABLE_HOT_POST_REPLY_CHECKS": False},
        )
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        assert server.xai_requests == []
        assert server.posts == []
        state = read_json(base_dir / "bot_state.json")
        assert "910" in state["skipped_quote_post_ids"]
    finally:
        server.stop()


def test_mentions_pagination_reaches_replyable_candidate_on_second_page(tmp_path: Path) -> None:
    mentions = [
        {"id": str(100 + i), "author_id": str(200 + i), "conversation_id": str(100 + i), "text": "@a @b @MrsMThatcher"}
        for i in range(5)
    ]
    mentions.append(
        {
            "id": "106",
            "author_id": "206",
            "conversation_id": "106",
            "text": "@MrsMThatcher This is a serious point worth answering",
        }
    )
    server = FakeApiServer(
        {
            "enable_pagination": True,
            "mentions": mentions,
            "grok_reply": "A serious point deserves a serious answer.",
        }
    ).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            local_config={"MAX_MENTIONS_PER_CHECK": 5, "MENTIONS_MAX_PAGES_PER_CHECK": 2},
        )
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout

        assert fake_server_post_replies(server) == ["106"]
        mention_requests = [r for r in server.requests if r["path"].endswith("/mentions")]
        assert len(mention_requests) == 2
        assert mention_requests[1]["query"]["pagination_token"] == ["5"]
    finally:
        server.stop()


def test_quote_tweet_pagination_reaches_unseen_candidate_on_second_page(tmp_path: Path) -> None:
    quote_tweets = [
        {
            "id": str(1000 + i),
            "author_id": str(3000 + i),
            "conversation_id": str(1000 + i),
            "created_at": "2026-01-01T00:00:00.000Z",
            "text": "Already seen quote",
            "referenced_tweets": [{"type": "quoted", "id": "900"}],
        }
        for i in range(10)
    ]
    quote_tweets.append(
        {
            "id": "1010",
            "author_id": "3010",
            "conversation_id": "1010",
            "created_at": "2026-01-01T00:00:00.000Z",
            "text": "This quote deserves a reply",
            "referenced_tweets": [{"type": "quoted", "id": "900"}],
        }
    )
    server = FakeApiServer(
        {
            "enable_pagination": True,
            "tweets": {"900": {"id": "900", "author_id": "12345", "text": "Original post"}},
            "quote_tweets": {"900": {"data": quote_tweets}},
            "grok_reply": "The point answers itself neatly enough.",
        }
    ).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "recent_own_post_ids": ["900"],
                "seen_quote_post_ids": [str(1000 + i) for i in range(10)],
                "next_reply_lane_priority": "quote",
            },
            local_config={
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "QUOTE_LOOKUP_API_MAX_RESULTS": 10,
                "QUOTE_LOOKUP_MAX_PAGES_PER_POST": 2,
            },
        )
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout

        assert fake_server_post_replies(server) == ["1010"]
        quote_requests = [r for r in server.requests if r["path"] == "/2/tweets/900/quote_tweets"]
        assert len(quote_requests) == 2
        assert quote_requests[1]["query"]["pagination_token"] == ["10"]
    finally:
        server.stop()


def test_hot_post_pagination_reaches_candidate_on_second_page(tmp_path: Path) -> None:
    replies = [
        {
            "id": str(300 + i),
            "author_id": str(400 + i),
            "conversation_id": "700",
            "created_at": "2026-01-01T00:00:00.000Z",
            "text": "already handled",
            "referenced_tweets": [{"type": "replied_to", "id": "700"}],
        }
        for i in range(10)
    ]
    replies.append(
            {
                "id": "311",
                "author_id": "411",
                "conversation_id": "700",
                "created_at": "2026-01-01T00:00:00.000Z",
                "text": "This watched post reply deserves an answer",
                "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
                "referenced_tweets": [{"type": "replied_to", "id": "700"}],
            }
    )
    server = FakeApiServer(
        {
            "enable_pagination": True,
            "mentions": [],
            "search_recent": replies,
            "grok_reply": "That is exactly the weakness in the argument.",
        }
    ).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            watch_ids=["700"],
            state={"replied_to_ids": [str(300 + i) for i in range(10)]},
            local_config={
                "HOT_POST_REPLY_SEARCH_API_MAX_RESULTS": 10,
                "HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK": 2,
            },
        )
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout

        assert fake_server_post_replies(server) == ["311"]
        search_requests = [r for r in server.requests if r["path"] == "/2/tweets/search/recent"]
        assert len(search_requests) == 2
        assert search_requests[1]["query"]["pagination_token"] == ["10"]
    finally:
        server.stop()


def test_mentions_truncated_pagination_does_not_advance_watermark(tmp_path: Path) -> None:
    mentions = [
        {"id": str(100 + i), "author_id": str(200 + i), "conversation_id": str(100 + i), "text": "@a @b @MrsMThatcher"}
        for i in range(16)
    ]
    server = FakeApiServer({"enable_pagination": True, "mentions": mentions}).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            local_config={"MAX_MENTIONS_PER_CHECK": 5, "MENTIONS_MAX_PAGES_PER_CHECK": 3},
        )
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        assert state.get("last_seen_mention_id") is None
        assert "Pagination truncated for mentions" in result.stdout
    finally:
        server.stop()


def test_mentions_truncated_pagination_resumes_on_next_check(tmp_path: Path) -> None:
    mentions = [
        {"id": str(100 + i), "author_id": str(200 + i), "conversation_id": str(100 + i), "text": "@a @b @MrsMThatcher"}
        for i in range(15)
    ]
    mentions.append(
        {
            "id": "116",
            "author_id": "216",
            "conversation_id": "116",
            "text": "@MrsMThatcher A fourth-page mention deserves an answer",
        }
    )
    server = FakeApiServer(
        {
            "enable_pagination": True,
            "mentions": mentions,
            "grok_reply": "The fourth-page point is still worth answering.",
        }
    ).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            local_config={"MAX_MENTIONS_PER_CHECK": 5, "MENTIONS_MAX_PAGES_PER_CHECK": 3},
        )
        first = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"})
        assert first.returncode == 0, first.stderr + first.stdout
        state = read_json(base_dir / "bot_state.json")
        assert state.get("last_seen_mention_id") is None
        assert state["mention_pagination"]["next_token"] == "15"
        assert fake_server_post_replies(server) == []

        second = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000002"})
        assert second.returncode == 0, second.stderr + second.stdout
        assert fake_server_post_replies(server) == ["116"]
        state = read_json(base_dir / "bot_state.json")
        assert state["mention_pagination"] == {}
        mention_requests = [r for r in server.requests if r["path"].endswith("/mentions")]
        assert mention_requests[3]["query"]["pagination_token"] == ["15"]
    finally:
        server.stop()


def test_successful_truncated_mention_reply_preserves_cursor_until_tail_is_drained(
    tmp_path: Path,
) -> None:
    mentions = [
        {
            "id": str(tweet_id),
            "author_id": str(400 + tweet_id),
            "conversation_id": str(tweet_id),
            "text": "@a @b @MrsMThatcher",
        }
        for tweet_id in (204, 203, 202, 201)
    ]
    mentions.append(
        {
            "id": "200",
            "author_id": "600",
            "conversation_id": "200",
            "text": "@MrsMThatcher The first-page point deserves an answer",
        }
    )
    mentions.append(
        {
            "id": "150",
            "author_id": "550",
            "conversation_id": "150",
            "text": "@MrsMThatcher The older continuation point deserves an answer too",
        }
    )
    server = FakeApiServer(
        {
            "enable_pagination": True,
            "mentions": mentions,
            "grok_replies": [
                "The first point warrants a concise answer in its own right.",
                "The older continuation point deserves separate consideration.",
            ],
        }
    ).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={"last_seen_mention_id": "99"},
            local_config={
                "MAX_MENTIONS_PER_CHECK": 5,
                "MENTIONS_MAX_PAGES_PER_CHECK": 1,
            },
        )

        first = run_bot_command(
            base_dir,
            server,
            "--test-cycle",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )
        assert first.returncode == 0, first.stderr + first.stdout
        assert fake_server_post_replies(server) == ["200"]
        state = read_json(base_dir / "bot_state.json")
        assert state["last_seen_mention_id"] == "99"
        assert state["mention_pagination"] == {
            "base_since_id": "99",
            "next_token": "5",
        }

        second = run_bot_command(
            base_dir,
            server,
            "--test-cycle",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000002"},
        )
        assert second.returncode == 0, second.stderr + second.stdout
        assert fake_server_post_replies(server) == ["200", "150"]
        state = read_json(base_dir / "bot_state.json")
        assert state["last_seen_mention_id"] == "150"
        assert state["mention_pagination"] == {}
        mention_requests = [
            request
            for request in server.requests
            if request["path"].endswith("/mentions")
        ]
        assert mention_requests[1]["query"]["since_id"] == ["99"]
        assert mention_requests[1]["query"]["pagination_token"] == ["5"]

        third = run_bot_command(
            base_dir,
            server,
            "--test-cycle",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000004"},
        )
        assert third.returncode == 0, third.stderr + third.stdout
        assert fake_server_post_replies(server) == ["200", "150"]
        state = read_json(base_dir / "bot_state.json")
        assert state["last_seen_mention_id"] == "204"
        assert state["mention_pagination"] == {}
    finally:
        server.stop()


def test_hot_post_truncated_pagination_does_not_advance_since_id(tmp_path: Path) -> None:
    replies = [
        {
            "id": str(300 + i),
            "author_id": str(400 + i),
            "conversation_id": "700",
            "created_at": "2026-01-01T00:00:00.000Z",
            "text": "Not actually a reply",
        }
        for i in range(31)
    ]
    server = FakeApiServer({"enable_pagination": True, "mentions": [], "search_recent": replies}).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            watch_ids=["700"],
            local_config={
                "HOT_POST_REPLY_SEARCH_API_MAX_RESULTS": 10,
                "HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK": 3,
            },
        )
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        assert state["hot_post_reply_since_ids"] == {}
        assert "Not updating hot-post reply since_id" in result.stdout
    finally:
        server.stop()


def test_hot_post_resumed_final_unusable_page_clears_cursor_and_advances_since_id(
    tmp_path: Path,
) -> None:
    replies = [
        {
            "id": str(tweet_id),
            "author_id": str(1000 + tweet_id),
            "conversation_id": "700",
            "created_at": "2026-01-01T00:00:00.000Z",
            "text": "Earlier page item",
        }
        for tweet_id in range(300, 288, -1)
    ]
    server = FakeApiServer(
        {
            "enable_pagination": True,
            "mentions": [],
            "search_recent": replies,
        }
    ).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            watch_ids=["700"],
            state={
                "hot_post_reply_since_ids": {"700": "250"},
                "hot_post_reply_pagination_tokens": {"700": "10"},
            },
            local_config={
                "HOT_POST_REPLY_SEARCH_API_MAX_RESULTS": 10,
                "HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK": 1,
                "HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS": 100,
            },
        )

        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        assert fake_server_post_replies(server) == []
        state = read_json(base_dir / "bot_state.json")
        assert state["hot_post_reply_since_ids"] == {"700": "290"}
        assert state["hot_post_reply_pagination_tokens"] == {}
        search_request = next(
            request
            for request in server.requests
            if request["path"] == "/2/tweets/search/recent"
        )
        assert search_request["query"]["since_id"] == ["250"]
        assert search_request["query"]["pagination_token"] == ["10"]
    finally:
        server.stop()


def test_hot_post_truncated_pagination_resumes_on_next_check(tmp_path: Path) -> None:
    replies = [
        {
            "id": str(300 + i),
            "author_id": str(400 + i),
            "conversation_id": "700",
            "created_at": "2026-01-01T00:00:00.000Z",
            "text": "already handled",
            "referenced_tweets": [{"type": "replied_to", "id": "700"}],
        }
        for i in range(30)
    ]
    replies.append(
            {
                "id": "330",
                "author_id": "430",
                "conversation_id": "700",
                "created_at": "2026-01-01T00:00:00.000Z",
                "text": "Fourth-page watched reply deserves an answer",
                "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
                "referenced_tweets": [{"type": "replied_to", "id": "700"}],
            }
    )
    server = FakeApiServer(
        {
            "enable_pagination": True,
            "mentions": [],
            "search_recent": replies,
            "grok_reply": "That watched reply deserves a short answer.",
        }
    ).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            watch_ids=["700"],
            state={"replied_to_ids": [str(300 + i) for i in range(30)]},
            local_config={
                "HOT_POST_REPLY_SEARCH_API_MAX_RESULTS": 10,
                "HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK": 3,
            },
        )
        first = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"})
        assert first.returncode == 0, first.stderr + first.stdout
        state = read_json(base_dir / "bot_state.json")
        assert state["hot_post_reply_since_ids"] == {}
        assert state["hot_post_reply_pagination_tokens"]["700"] == "30"
        assert fake_server_post_replies(server) == []

        second = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000002"})
        assert second.returncode == 0, second.stderr + second.stdout
        assert fake_server_post_replies(server) == ["330"]
        state = read_json(base_dir / "bot_state.json")
        assert state["hot_post_reply_pagination_tokens"] == {}
        search_requests = [r for r in server.requests if r["path"] == "/2/tweets/search/recent"]
        assert search_requests[3]["query"]["pagination_token"] == ["30"]
    finally:
        server.stop()


def test_api_page_size_is_not_bypassed_by_processing_caps(tmp_path: Path) -> None:
    server = FakeApiServer(
        {
            "mentions": [],
            "search_recent": [],
            "tweets": {"900": {"id": "900", "author_id": "12345", "text": "Original post"}},
            "quote_tweets": {"900": {}},
        }
    ).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            watch_ids=["700"],
            state={"next_reply_lane_priority": "quote", "recent_own_post_ids": ["900"]},
            local_config={
                "HOT_POST_REPLY_SEARCH_API_MAX_RESULTS": 10,
                "MAX_HOT_POST_REPLIES_PER_CHECK": 500,
                "QUOTE_LOOKUP_API_MAX_RESULTS": 10,
                "MAX_QUOTE_POSTS_PER_CHECK": 500,
            },
        )
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout

        search_request = next(req for req in server.requests if req["path"] == "/2/tweets/search/recent")
        quote_request = next(req for req in server.requests if req["path"] == "/2/tweets/900/quote_tweets")
        assert search_request["query"]["max_results"] == ["10"]
        assert quote_request["query"]["max_results"] == ["10"]
    finally:
        server.stop()


def test_quote_lookup_truncated_pagination_resumes_on_next_check(tmp_path: Path) -> None:
    quote_tweets = [
        {
            "id": str(1000 + i),
            "author_id": str(3000 + i),
            "conversation_id": str(1000 + i),
            "created_at": "2026-01-01T00:00:00.000Z",
            "text": "Already seen quote",
            "referenced_tweets": [{"type": "quoted", "id": "900"}],
        }
        for i in range(30)
    ]
    quote_tweets.append(
        {
            "id": "1030",
            "author_id": "3030",
            "conversation_id": "1030",
            "created_at": "2026-01-01T00:00:00.000Z",
            "text": "Fourth page quote deserves a reply",
            "referenced_tweets": [{"type": "quoted", "id": "900"}],
        }
    )
    server = FakeApiServer(
        {
            "enable_pagination": True,
            "tweets": {"900": {"id": "900", "author_id": "12345", "text": "Original post"}},
            "quote_tweets": {"900": {"data": quote_tweets}},
            "grok_reply": "That fourth-page point is worth answering.",
        }
    ).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "recent_own_post_ids": ["900"],
                "seen_quote_post_ids": [str(1000 + i) for i in range(30)],
                "next_reply_lane_priority": "quote",
            },
            local_config={
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "QUOTE_LOOKUP_API_MAX_RESULTS": 10,
                "QUOTE_LOOKUP_MAX_PAGES_PER_POST": 3,
                "MIN_SECONDS_BETWEEN_REPLIES": 1,
            },
        )
        first = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"})
        assert first.returncode == 0, first.stderr + first.stdout
        state = read_json(base_dir / "bot_state.json")
        assert state["quote_lookup_pagination_tokens"]["900"] == "30"
        assert fake_server_post_replies(server) == []

        second = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000002"})
        assert second.returncode == 0, second.stderr + second.stdout
        assert fake_server_post_replies(server) == ["1030"]
        state = read_json(base_dir / "bot_state.json")
        assert state["quote_lookup_pagination_tokens"] == {}
        quote_requests = [r for r in server.requests if r["path"] == "/2/tweets/900/quote_tweets"]
        assert quote_requests[3]["query"]["pagination_token"] == ["30"]
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
    assert len(fake_server.xai_requests) == 3
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


@pytest.mark.parametrize(("prior_count", "expected_posts"), [(0, 1), (1, 1), (5, 1), (6, 0)])
@pytest.mark.parametrize("fake_server", ["quote_tweet_reply.json"], indirect=True)
def test_per_author_cap_applies_to_quote_tweet_path(
    tmp_path: Path,
    fake_server: FakeApiServer,
    prior_count: int,
    expected_posts: int,
) -> None:
    base_dir = prepare_base_dir(
        tmp_path,
        state={
            "recent_own_post_ids": ["900"],
            "last_reply_epoch": 0,
            "daily_reply_date": datetime.now().strftime("%Y-%m-%d"),
            "daily_reply_count": prior_count,
            "daily_replied_author_ids": ["310"] if prior_count else [],
            "daily_replied_author_counts": {"310": prior_count} if prior_count else {},
        },
        local_config={"ENABLE_HOT_POST_REPLY_CHECKS": False},
    )

    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    assert len(fake_server.posts) == expected_posts
    state = read_json(base_dir / "bot_state.json")
    if prior_count < 6:
        assert state["daily_replied_author_counts"]["310"] == prior_count + 1
    else:
        assert fake_server.xai_requests == []
        assert state["daily_replied_author_counts"]["310"] == 6
        assert state["tweet_cache"]["910"]["post_type"] == "author_cap_quote_context"
        assert state["tweet_cache"]["910"]["referenced_tweets"] == [{"type": "quoted", "id": "900"}]
        assert state["tweet_cache"]["900"]["text"]


@pytest.mark.parametrize("fake_server", ["grok_skip.json"], indirect=True)
def test_grok_skip_does_not_post(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path)
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.posts == []
    state = read_json(base_dir / "bot_state.json")
    assert state["last_reply_epoch"] == 0
    assert state["last_seen_mention_id"] == "130"
    assert "130" not in state["replied_to_ids"]


@pytest.mark.parametrize("fake_server", ["per_author_cap.json"], indirect=True)
def test_per_author_cap_skips_seventh_reply(tmp_path: Path, fake_server: FakeApiServer) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    base_dir = prepare_base_dir(
        tmp_path,
        state={
            "daily_reply_date": today,
            "daily_reply_count": 6,
            "daily_replied_author_ids": ["240"],
            "daily_replied_author_counts": {"240": 6},
            "last_reply_epoch": 0,
        },
    )
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.posts == []
    assert fake_server.xai_requests == []
    state = read_json(base_dir / "bot_state.json")
    assert state["last_seen_mention_id"] == "140"
    assert state["tweet_cache"]["140"]["post_type"] == "author_cap_context"
    assert state["tweet_cache"]["140"]["text"]


def test_author_cap_context_survives_restart_in_newer_target_prompt(
    tmp_path: Path,
) -> None:
    first_epoch = 2_000_000_000
    second_epoch = first_epoch + 86_400
    london = ZoneInfo("Europe/London")
    first_date = datetime.fromtimestamp(first_epoch, london).strftime("%Y-%m-%d")
    second_date = datetime.fromtimestamp(second_epoch, london).strftime("%Y-%m-%d")
    assert second_date != first_date

    capped_text = "@MrsMThatcher Older capped contribution about responsibility."
    newer_text = "@MrsMThatcher Newer contribution asking what responsibility requires."
    server = FakeApiServer(
        {
            "mention_responses": [
                [{
                    "id": "500",
                    "text": capped_text,
                    "author_id": "240",
                    "conversation_id": "500",
                    "created_at": "2026-06-30T12:00:00Z",
                    "referenced_tweets": [],
                }],
                [{
                    "id": "510",
                    "text": newer_text,
                    "author_id": "240",
                    "conversation_id": "500",
                    "created_at": "2026-06-30T12:05:00Z",
                    "referenced_tweets": [{"type": "replied_to", "id": "500"}],
                }],
            ],
            "grok_replies": ["Responsibility should be matched by sound judgement."],
        }
    ).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "daily_reply_date": first_date,
                "daily_reply_count": 1,
                "daily_replied_author_ids": ["240"],
                "daily_replied_author_counts": {"240": 1},
                "last_reply_epoch": 0,
            },
            local_config={
                "MAX_REPLIES_PER_AUTHOR_PER_DAY": 1,
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "ENABLE_QUOTE_TWEET_CHECKS": False,
            },
        )

        process_a = run_bot_command(
            base_dir,
            server,
            "--test-cycle",
            extra_env={"MRS_FAKE_NOW_EPOCH": str(first_epoch)},
        )
        assert process_a.returncode == 0, process_a.stderr + process_a.stdout
        assert server.xai_requests == []
        assert server.posts == []
        state_after_a = read_json(base_dir / "bot_state.json")
        assert state_after_a["tweet_cache"]["500"]["post_type"] == "author_cap_context"
        assert state_after_a["tweet_cache"]["500"]["text"] == capped_text

        process_b = run_bot_command(
            base_dir,
            server,
            "--test-cycle",
            extra_env={"MRS_FAKE_NOW_EPOCH": str(second_epoch)},
        )
        assert process_b.returncode == 0, process_b.stderr + process_b.stdout

        proposer_requests = [
            request
            for request in server.xai_requests
            if request.get("response_format", {}).get("json_schema", {}).get("name")
            == "ai_reply_proposer"
        ]
        assert len(proposer_requests) == 1
        user_content = proposer_requests[0]["messages"][-1]["content"]
        if isinstance(user_content, list):
            user_content = next(
                item["text"]
                for item in user_content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        proposer_payload = json.loads(user_content)
        sections = proposer_payload["context_sections"]
        assert sections["incoming_contribution_to_answer"] == newer_text
        assert sections["quoted_post_context_only"] is None
        assert sections["bounded_parent_thread_context_only"] == [{
            "post_id": "500",
            "author_role": "user",
            "text": capped_text,
        }]
        assert json.dumps(proposer_payload, sort_keys=True).count(capped_text) == 1
        assert proposer_payload["target"]["target_id"] == "510"
        assert proposer_payload["target"]["thread_id"] == "500"
        assert fake_server_post_replies(server) == ["510"]
        assert all(reply_target != "500" for reply_target in fake_server_post_replies(server))
    finally:
        server.stop()


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
                {
                    "id": "103",
                    "text": "@mrsMThatcher fourth",
                    "author_id": "240",
                    "conversation_id": "103",
                    "created_at": "2026-06-30T12:03:00Z",
                },
            ],
            "grok_replies": ["First reply.", "Second reply.", "Third reply.", "Fourth reply should not be used."],
        }
    ).start()
    try:
        base_dir = prepare_base_dir(tmp_path, local_config={"MAX_REPLIES_PER_AUTHOR_PER_DAY": 3})

        first = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"})
        assert first.returncode == 0, first.stderr + first.stdout
        second = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000002"})
        assert second.returncode == 0, second.stderr + second.stdout
        third = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000004"})
        assert third.returncode == 0, third.stderr + third.stdout
        fourth = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000006"})
        assert fourth.returncode == 0, fourth.stderr + fourth.stdout

        assert len(server.posts) == 3
        assert server.posts[0]["reply"]["in_reply_to_tweet_id"] == "100"
        assert server.posts[1]["reply"]["in_reply_to_tweet_id"] == "101"
        assert server.posts[2]["reply"]["in_reply_to_tweet_id"] == "102"
        assert len(server.xai_requests) == 9
        state = read_json(base_dir / "bot_state.json")
        assert state["daily_replied_author_counts"]["240"] == 3
        assert state["daily_replied_author_ids"] == ["240"]
        assert state["last_seen_mention_id"] == "103"
    finally:
        server.stop()


@pytest.mark.parametrize("fake_server", ["daily_cap.json"], indirect=True)
def test_daily_cap_skips_before_fetching_mentions(tmp_path: Path, fake_server: FakeApiServer) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    base_dir = prepare_base_dir(
        tmp_path,
        local_config={"MAX_AUTO_REPLIES_PER_DAY": 48},
        state={"daily_reply_date": today, "daily_reply_count": 48, "last_reply_epoch": 0},
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
    assert read_json(base_dir / "images_used.json") == ["t01.jpg"]
    assert state["last_regular_image_filename"] == "t01.jpg"


def test_distinct_upload_origin_receives_only_media_while_api_receives_tweet(
    tmp_path: Path,
) -> None:
    api_server = FakeApiServer(
        {"next_post_id": 901500}
    ).start()
    upload_server = FakeApiServer(
        {"v2_media_id": "distinct-upload-media"}
    ).start()
    try:
        base_dir = prepare_base_dir(tmp_path)
        result = run_bot_command(
            base_dir,
            api_server,
            "--test-post-quote",
            x_api_base_url=api_server.url,
            x_upload_base_url=upload_server.url,
            xai_api_base_url=f"{api_server.url}/v1",
        )

        assert result.returncode == 0, result.stderr + result.stdout
        assert upload_server.path_counts["/2/media/upload"] == 1
        assert upload_server.posts == []
        assert api_server.path_counts.get("/2/media/upload", 0) == 0
        assert len(api_server.posts) == 1
        assert api_server.posts[0]["media"]["media_ids"] == [
            "distinct-upload-media"
        ]
    finally:
        upload_server.stop()
        api_server.stop()


def test_unset_upload_origin_inherits_api_origin_for_media_and_tweet(
    tmp_path: Path,
) -> None:
    api_server = FakeApiServer(
        {"next_post_id": 901600, "v2_media_id": "inherited-media"}
    ).start()
    try:
        base_dir = prepare_base_dir(tmp_path)
        result = run_bot_command(
            base_dir,
            api_server,
            "--test-post-quote",
            x_api_base_url=api_server.url,
            xai_api_base_url=f"{api_server.url}/v1",
            unset_x_upload_base_url=True,
        )

        assert result.returncode == 0, result.stderr + result.stdout
        assert api_server.path_counts["/2/media/upload"] == 1
        assert len(api_server.posts) == 1
        assert api_server.posts[0]["media"]["media_ids"] == [
            "inherited-media"
        ]
    finally:
        api_server.stop()


def test_distinct_upload_origin_never_receives_x_reads(tmp_path: Path) -> None:
    api_server = FakeApiServer({}).start()
    upload_server = FakeApiServer({}).start()
    try:
        base_dir = prepare_base_dir(tmp_path)
        result = run_bot_command(
            base_dir,
            api_server,
            "--test-cycle",
            x_api_base_url=api_server.url,
            x_upload_base_url=upload_server.url,
            xai_api_base_url=f"{api_server.url}/v1",
        )

        assert result.returncode == 0, result.stderr + result.stdout
        assert any(
            request["method"] == "GET" for request in api_server.requests
        )
        assert upload_server.requests == []
    finally:
        upload_server.stop()
        api_server.stop()


def test_test_post_quote_replays_receipt_without_second_post(tmp_path: Path) -> None:
    server = FakeApiServer({"next_post_id": 950000}).start()
    try:
        base_dir = prepare_base_dir(tmp_path)
        quote_hash = hashlib.sha256(collapse_quote_whitespace("A test quote.").encode("utf-8")).hexdigest()
        write_private_json(
            base_dir / "regular_post_receipt.json",
            {
                "schema_version": 1,
                "post_id": "940001",
                "quote_hash": quote_hash,
                "image_basename": "t01.jpg",
                "quote_post_epoch": 1_800_000_000,
                "next_quote_post_epoch": 1_800_007_200,
                "text": "A test quote.",
            },
        )

        result = run_bot_command(base_dir, server, "--test-post-quote")

        assert result.returncode == 0, result.stderr + result.stdout
        assert server.posts == []
        assert not (base_dir / "regular_post_receipt.json").exists()
        state = read_json(base_dir / "bot_state.json")
        assert state["last_main_post_id"] == "940001"
        assert state["next_quote_post_epoch"] == 1_800_007_200
    finally:
        server.stop()


@pytest.mark.parametrize("fake_server", ["media_v2_fallback.json"], indirect=True)
def test_media_v2_server_failure_does_not_fallback_to_v1_upload(
    tmp_path: Path,
    fake_server: FakeApiServer,
) -> None:
    base_dir = prepare_base_dir(tmp_path)
    result = run_bot_command(base_dir, fake_server, "--test-post-quote")

    assert result.returncode == 1
    assert fake_server.path_counts["/2/media/upload"] == 1
    assert fake_server.path_counts.get("/1.1/media/upload.json", 0) == 0
    assert fake_server.posts == []


def test_media_v2_rate_limit_does_not_fallback_to_v1_upload(tmp_path: Path) -> None:
    server = FakeApiServer({"v2_media_status": 429}).start()
    try:
        base_dir = prepare_base_dir(tmp_path)
        result = run_bot_command(base_dir, server, "--test-post-quote")

        assert result.returncode == 1
        assert server.path_counts["/2/media/upload"] == 1
        assert server.path_counts.get("/1.1/media/upload.json", 0) == 0
        assert server.posts == []
        state = read_json(base_dir / "bot_state.json")
        assert state["x_write_api_cooldown_reason"] == "write/x returned 429/rate limit"
        assert int(state["x_write_api_cooldown_until_epoch"]) > 0
        assert state["api_cooldown_until_epoch"] == 0
    finally:
        server.stop()


def test_quote_image_post_missing_created_post_id_fails_without_marking_assets_used(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIOS / "media_quote_post.json")
    scenario["tweet_post_responses"] = [{"status": 201, "body": {"data": {}}}]
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(tmp_path)
        lines_before = json.loads((base_dir / "lines_used.json").read_text())
        images_before = json.loads((base_dir / "images_used.json").read_text())
        result = run_bot_command(base_dir, server, "--test-post-quote")

        assert result.returncode == 1
        assert len(server.posts) == 1
        assert json.loads((base_dir / "lines_used.json").read_text()) == lines_before
        assert json.loads((base_dir / "images_used.json").read_text()) == images_before
        state = read_json(base_dir / "bot_state.json")
        assert not state.get("last_main_post_id")
        assert state.get("recent_own_post_ids", []) == []
        assert len(state["x_write_error_epochs"]) == 1
        assert state["x_error_epochs"] == []
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
        assert int(state.get("next_meme_post_epoch", 0)) > 0
        assert state.get("next_meme_schedule_mode") in {"fallback_startup", "fallback_migrated"}
        assert len(state["x_write_error_epochs"]) == 1
        assert state["x_error_epochs"] == []
    finally:
        server.stop()


@pytest.mark.parametrize("fake_server", ["made_with_ai_retry.json"], indirect=True)
def test_made_with_ai_post_rejection_is_not_retried_without_provider_contract(
    tmp_path: Path,
    fake_server: FakeApiServer,
) -> None:
    base_dir = prepare_base_dir(tmp_path, local_config={"MARK_AI_REPLIES_AS_AI": True})
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.path_counts["/2/tweets"] == 1
    assert fake_server.posts == []
    state = read_json(base_dir / "bot_state.json")
    assert "180" not in state.get("replied_to_ids", [])
    receipt = read_json(base_dir / "confirmed_reply_receipt.json")
    assert receipt["lifecycle_state"] == "sending"
    assert receipt["target_id"] == "180"


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
        assert state["x_write_error_epochs"]
        assert state["x_error_epochs"] == []
    finally:
        server.stop()


@pytest.mark.parametrize("fake_server", ["reply_not_allowed_403.json"], indirect=True)
def test_reply_not_allowed_403_remains_ambiguous_and_unhandled(
    tmp_path: Path,
    fake_server: FakeApiServer,
) -> None:
    base_dir = prepare_base_dir(tmp_path)
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.posts == []
    state = read_json(base_dir / "bot_state.json")
    assert "190" not in state["replied_to_ids"]
    assert state["daily_reply_count"] == 0
    assert state["x_error_epochs"] == []
    assert state["x_write_error_epochs"] == []
    assert "reply_evaluation_records" not in state
    marker = read_json(base_dir / "ambiguous_post_outcome.json")
    assert marker["outcome"] == "ambiguous_remote_post"
    assert marker["reply_to_id"] == "190"


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


@pytest.mark.parametrize("fake_server", ["repeated_x_errors.json"], indirect=True)
def test_x_read_cooldown_does_not_block_quote_image_posting(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path)

    for _ in range(3):
        result = run_cycle(base_dir, fake_server)
        assert result.returncode == 0, result.stderr + result.stdout

    state = read_json(base_dir / "bot_state.json")
    assert len(state["x_error_epochs"]) == 3
    assert int(state["api_cooldown_until_epoch"]) > int(datetime.now().timestamp())
    assert state["x_write_api_cooldown_until_epoch"] == 0

    post_result = run_bot_command(base_dir, fake_server, "--test-post-quote")
    assert post_result.returncode == 0, post_result.stderr + post_result.stdout
    assert any(post.get("media", {}).get("media_ids") for post in fake_server.posts)


def test_x_read_cooldown_does_not_block_due_daily_meme(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "repeated_x_errors.json")).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            meme=True,
            local_config={"ENABLE_DAILY_MEME_POSTS": True, "MEME_POST_TEXT": "meme"},
            state={
                "next_quote_post_epoch": 2_000_100_000,
                "last_quote_post_epoch": 1_999_900_000,
                "next_meme_post_epoch": 2_000_000_000,
                "meme_schedule_version": 2,
                "next_meme_schedule_mode": "fallback",
                "next_meme_schedule_date": "2033-05-18",
            },
        )

        for offset in [0, 10, 20]:
            result = run_bot_command(
                base_dir,
                server,
                "--test-cycle",
                extra_env={"MRS_FAKE_NOW_EPOCH": str(2_000_000_000 + offset)},
            )
            assert result.returncode == 0, result.stderr + result.stdout

        state = read_json(base_dir / "bot_state.json")
        assert int(state["api_cooldown_until_epoch"]) > 2_000_000_000
        assert state["x_write_api_cooldown_until_epoch"] == 0

        env = base_test_env()
        env.update(
            {
                "MRS_TEST_MODE": "1",
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
                "MRS_FAKE_NOW_EPOCH": "2000000030",
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
        try:
            deadline = time.time() + 10
            while time.time() < deadline:
                if any(post.get("text") == "meme" for post in server.posts):
                    break
                if proc.poll() is not None:
                    stdout, stderr = proc.communicate(timeout=1)
                    raise AssertionError(stdout + stderr)
                time.sleep(0.1)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        assert any(post.get("text") == "meme" for post in server.posts)
    finally:
        server.stop()


@pytest.mark.parametrize("fake_server", ["xai_failure.json"], indirect=True)
def test_xai_failure_records_xai_error_without_posting(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path)
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    state = read_json(base_dir / "bot_state.json")
    assert len(state["xai_error_epochs"]) == 1
    assert state["x_error_epochs"] == []
    assert fake_server.posts == []


@pytest.mark.parametrize("fake_server", ["xai_failure.json"], indirect=True)
def test_xai_cooldown_does_not_block_quote_image_posting(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path, local_config={"MAX_XAI_ERRORS_PER_WINDOW": 3})

    for offset in [0, 10, 20]:
        result = run_bot_command(
            base_dir,
            fake_server,
            "--test-cycle",
            extra_env={"MRS_FAKE_NOW_EPOCH": str(2_000_000_000 + offset)},
        )
        assert result.returncode == 0, result.stderr + result.stdout

    state = read_json(base_dir / "bot_state.json")
    assert len(state["xai_error_epochs"]) == 3
    assert state["xai_api_cooldown_reason"] == "too many xai API errors in the last hour"
    assert int(state["xai_api_cooldown_until_epoch"]) > 2_000_000_000
    assert state["api_cooldown_until_epoch"] == 0

    post_result = run_bot_command(
        base_dir,
        fake_server,
        "--test-post-quote",
        extra_env={"MRS_FAKE_NOW_EPOCH": "2000000030"},
    )
    assert post_result.returncode == 0, post_result.stderr + post_result.stdout
    assert any(post.get("media", {}).get("media_ids") for post in fake_server.posts)


def test_hot_post_search_failure_does_not_discard_mentions(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIOS / "normal_mention_reply.json")
    scenario["error_paths"] = {"/2/tweets/search/recent": 503}
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(tmp_path, watch_ids=["700"])
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout

        state = read_json(base_dir / "bot_state.json")
        assert state["replied_to_ids"] == ["100"]
        assert len(state["quote_x_error_epochs"]) == 1
        assert state["x_error_epochs"] == []
        assert state["api_cooldown_until_epoch"] == 0
        assert fake_server_post_replies(server) == ["100"]
    finally:
        server.stop()


def test_hot_post_breaker_suppresses_subsequent_search_calls(tmp_path: Path) -> None:
    scenario = {
        "mentions": [],
        "error_paths": {"/2/tweets/search/recent": 503},
    }
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            watch_ids=["700"],
            local_config={"MAX_X_ERRORS_PER_WINDOW": 3},
        )

        for offset in [0, 10, 20]:
            result = run_bot_command(
                base_dir,
                server,
                "--test-cycle",
                extra_env={"MRS_FAKE_NOW_EPOCH": str(2_000_000_000 + offset)},
            )
            assert result.returncode == 0, result.stderr + result.stdout

        state = read_json(base_dir / "bot_state.json")
        assert len(state["quote_x_error_epochs"]) == 3
        assert int(state["quote_api_cooldown_until_epoch"]) > 2_000_000_000
        assert server.path_counts.get("/2/tweets/search/recent") == 3

        blocked = run_bot_command(
            base_dir,
            server,
            "--test-cycle",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000030"},
        )
        assert blocked.returncode == 0, blocked.stderr + blocked.stdout
        assert server.path_counts.get("/2/tweets/search/recent") == 3
    finally:
        server.stop()


def test_test_mode_refuses_production_base_dir(tmp_path: Path) -> None:
    env = base_test_env()
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
    env = base_test_env()
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
    env = base_test_env()
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
    env = base_test_env()
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


def test_self_test_refuses_live_defaults_and_upload_inherits_fake_x_origin(
    tmp_path: Path,
) -> None:
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

    inherited_upload = run_bot_with_env(
        base_dir,
        "--self-test",
        extra_env={
            "X_API_BASE_URL": fake,
            "XAI_API_BASE_URL": f"{fake}/v1",
        },
    )
    assert inherited_upload.returncode == 0, (
        inherited_upload.stderr + inherited_upload.stdout
    )

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
def test_x_endpoint_overrides_reject_terminal_version_segments(
    tmp_path: Path,
    fake_server: FakeApiServer,
) -> None:
    base_dir = prepare_base_dir(tmp_path)
    x_result = run_bot_command(
        base_dir,
        fake_server,
        "--test-cycle",
        x_api_base_url=f"{fake_server.url}/2",
        x_upload_base_url=fake_server.url,
        xai_api_base_url=f"{fake_server.url}/v1",
    )
    upload_result = run_bot_command(
        base_dir,
        fake_server,
        "--test-cycle",
        x_api_base_url=fake_server.url,
        x_upload_base_url=f"{fake_server.url}/1.1",
        xai_api_base_url=f"{fake_server.url}/v1",
    )

    assert x_result.returncode != 0
    assert "X API bases must be origin-only" in x_result.stderr
    assert upload_result.returncode != 0
    assert "X API bases must be origin-only" in upload_result.stderr
    assert fake_server.posts == []


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
        ({"xai_success_body": {"choices": [{"message": {"content": ""}}]}}, True, False),
        ({"xai_success_body": {"choices": [{"message": {"content": "word " * 200}}]}}, True, False),
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
        assert state.get("last_seen_mention_id") is None
        assert state.get("reply_evaluation_records", {}) == {}
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


def test_expired_api_cooldown_is_cleared_on_startup(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "api_cooldown_until_epoch": 900,
                "api_cooldown_reason": "expired test cooldown",
                "quote_api_cooldown_until_epoch": 800,
                "quote_api_cooldown_reason": "expired quote test cooldown",
                "last_reply_epoch": 1_000,
            },
        )
        result = run_bot_command(base_dir, server, extra_env={"MRS_FAKE_NOW_EPOCH": "1000"})

        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        assert state["api_cooldown_until_epoch"] == 0
        assert state["api_cooldown_reason"] == ""
        assert state["quote_api_cooldown_until_epoch"] == 0
        assert state["quote_api_cooldown_reason"] == ""
    finally:
        server.stop()


def test_invalid_reply_lane_priority_is_normalized_on_startup(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "next_reply_lane_priority": "sideways",
                "last_reply_epoch": 1_000,
            },
        )
        result = run_bot_command(
            base_dir,
            server,
            extra_env={"MRS_FAKE_NOW_EPOCH": "1000"},
        )

        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        assert state["next_reply_lane_priority"] == "normal"
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
        assert "## Input files" in digest.stdout
        assert "records_after_since=" in digest.stdout
        assert "records_in_window_before_dedupe=" in digest.stdout
        assert "1 mention reply" in digest.stdout
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
    assert "unknown" in ops_digest.stdout
    assert "mentions/hot-post" not in ops_digest.stdout
    assert "not quota exhaustion" in ops_digest.stdout
    assert "Used-history migrations" in ops_digest.stdout
    assert "Used-history normalizations" in ops_digest.stdout

    classified_base = prepare_base_dir(tmp_path / "digest-classified-errors")
    (classified_base / "test.log").write_text(
        "\n".join(
            [
                "2026-07-03 10:00:00 CRITICAL <module>:799 - Missing X credentials. Set X_CONSUMER_KEY, X_CONSUMER_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET, X_MY_USER_ID",
                "2026-07-03 10:00:00 CRITICAL <module>:807 - ENABLE_AUTO_REPLIES is True, but XAI_API_KEY is not set.",
                "2026-07-03 10:00:00 ERROR    run_self_test:4317 - SELFTEST FAIL: X_CONSUMER_KEY set",
                "2026-07-03 10:00:00 ERROR    run_self_test:4317 - SELFTEST FAIL: X_CONSUMER_SECRET set",
                "2026-07-03 10:00:00 ERROR    run_self_test:4317 - Self-test finished with 2 failure(s)",
                "2026-07-03 10:15:00 ERROR    x_request:1254 - X API error 503: {\"detail\":\"Service Unavailable\"}",
                "2026-07-03 10:15:00 ERROR    maybe_reply_to_mentions:3091 - Failed to get mention/hot-post reply candidates\\nTraceback omitted",
                "2026-07-03 10:15:00 WARNING  print_rate_limit_headers:1194 - Rate Limit: 40000",
                "2026-07-03 10:15:00 WARNING  print_rate_limit_headers:1195 - Remaining: 40000",
                "2026-07-03 10:15:00 WARNING  record_api_error:1159 - Recorded x API error. status_code=503 errors_in_window=1/3 reset_epoch=1783050931 error=X API error 503: {\"detail\":\"Service Unavailable\"}",
                "2026-07-03 10:30:00 ERROR    x_bearer_request:1544 - X bearer API error 403: {\"detail\":\"You are not allowed to reply to this Tweet as the Tweet author has restricted who can reply\"}",
                "2026-07-03 10:30:01 WARNING  maybe_reply_to_quote_tweets:4086 - Quote tweet 999 reply not allowed; marking quote tweet as skipped without consuming reply quota",
                "2026-07-03 10:30:02 INFO     main:4111 - Config: MAX_MENTIONS_PER_CHECK=5",
                "2026-07-03 10:30:02 INFO     main:4112 - Config: MENTIONS_MAX_PAGES_PER_CHECK=3",
                "2026-07-03 10:30:02 INFO     main:4140 - Config: QUOTE_LOOKUP_API_MAX_RESULTS=10",
                "2026-07-03 10:30:02 INFO     main:4141 - Config: QUOTE_LOOKUP_MAX_PAGES_PER_POST=3",
                "2026-07-03 10:30:02 INFO     main:4148 - Config: HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK=3",
                "2026-07-03 10:31:00 DEBUG    save_state:994 - State being saved: {\"api_cooldown_until_epoch\": 0, \"xai_api_cooldown_until_epoch\": 0, \"quote_api_cooldown_until_epoch\": 0}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    classified_digest = run_digest(classified_base)
    assert classified_digest.returncode == 0, classified_digest.stderr
    assert "current health: no unresolved operational incidents" in (
        classified_digest.stdout
    )
    assert "## Transient provider observations" in classified_digest.stdout
    assert "Provider recovery is unverified" in classified_digest.stdout
    assert "x api transient failure" in classified_digest.stdout
    assert (
        "## Historical/resolved incident errors\n"
        "None identified in the selected window."
    ) in classified_digest.stdout
    assert "1 handled API restriction incident" in classified_digest.stdout
    assert "self-test failures: 2 check(s)" in classified_digest.stdout
    assert "Handled API restrictions" in classified_digest.stdout
    assert "post/reply" in classified_digest.stdout
    assert "403 restriction summary" in classified_digest.stdout
    assert "503/5xx summary" in classified_digest.stdout
    assert "SELFTEST FAIL: X_CONSUMER_KEY set" in classified_digest.stdout
    assert "MAX_MENTIONS_PER_CHECK=10" in classified_digest.stdout
    assert "MENTIONS_MAX_PAGES_PER_CHECK=3" not in classified_digest.stdout
    assert "QUOTE_LOOKUP_MAX_PAGES_PER_POST=3" not in classified_digest.stdout
    assert "HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK=3" not in classified_digest.stdout

    main_post_recovery_base = prepare_base_dir(tmp_path / "digest-main-post-recovery")
    (main_post_recovery_base / "test.log").write_text(
        "\n".join(
            [
                "2026-07-03 11:00:00 INFO     main:6600 - Config: MAX_QUOTE_IMAGE_PAIR_ATTEMPTS=25",
                "2026-07-03 11:00:00 INFO     main:6601 - Config: IMAGE_STRONG_MISMATCH_PENALTY=-10000.0",
                "2026-07-03 11:01:00 INFO     select_quote_candidate:4230 - Selected quote line_no=12 quote_hash=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa weight=1.50 seasonal_boost=True",
                "2026-07-03 11:01:01 INFO     choose_matched_unused_image:4490 - Image cycle status: used_count=4 currently_eligible=10 remaining_count=6 seasonally_excluded=1 stale_excluded=2 cycle_reset=False",
                "2026-07-03 11:01:02 INFO     choose_matched_unused_image:4534 - Selected matched image basename=t09.jpg image_no=8 score=7.25 components={'topic': 3.0}",
                "2026-07-03 11:01:03 INFO     post_random_quote:4730 - EVENT {\"event\":\"main_post_posted\",\"image_basename\":\"t09.jpg\",\"image_no\":8,\"image_score\":7.25,\"lane\":\"quote_image\",\"line_no\":12,\"post_id\":\"2073000000000000001\"}",
                "2026-07-03 11:01:04 INFO     post_random_quote:4740 - Quote text='A metadata matched quote.'",
                "2026-07-03 11:01:05 WARNING  write_regular_post_receipt:3501 - Wrote confirmed regular-post receipt pending local reconciliation: /tmp/regular_post_receipt.json",
                "2026-07-03 11:01:06 INFO     remove_regular_post_receipt:3602 - Removed reconciled regular-post receipt: /tmp/regular_post_receipt.json",
                "2026-07-03 11:01:07 INFO     post_random_quote:4741 - Quote/image posted successfully. posted_id=2073000000000000001",
                "2026-07-03 11:02:00 WARNING  quote_candidates_for_current_cycle:4187 - Skipping unanalysed current quote line_no=99 quote_hash=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb until quote analysis is refreshed",
                "2026-07-03 11:02:01 WARNING  quote_candidates_for_current_cycle:4281 - Quote cycle is exhausted by currently nonselectable quote(s); resetting quote cycle. unused_non_empty=1 full_selectable=12 full_hard_excluded=0",
                "2026-07-03 11:03:00 CRITICAL post_random_quote:4671 - Confirmed regular quote/image post_id=2073000000000000002 but failed writing recovery receipt; in-memory used histories remain marked",
                "2026-07-03 11:04:00 CRITICAL run_test_post_quote:7005 - REMOTE X POST WAS CONFIRMED; DO NOT RETRY MANUALLY. Test quote/image local persistence/recovery needs attention.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    main_post_recovery_digest = run_digest(main_post_recovery_base)
    assert main_post_recovery_digest.returncode == 0, main_post_recovery_digest.stderr
    assert "1 quote/image post" in main_post_recovery_digest.stdout
    assert "2 confirmed-post recovery records in window" in (
        main_post_recovery_digest.stdout
    )
    assert "1 asset-metadata warning in window" in (
        main_post_recovery_digest.stdout
    )
    assert "Transactional receipt lifecycle" in main_post_recovery_digest.stdout
    assert "regular_written" in main_post_recovery_digest.stdout
    assert "regular_removed" in main_post_recovery_digest.stdout
    assert "Confirmed remote posts with local recovery/persistence trouble" in main_post_recovery_digest.stdout
    assert "DO NOT RETRY MANUALLY" in main_post_recovery_digest.stdout
    assert "Asset metadata health" in main_post_recovery_digest.stdout
    assert "Skipping unanalysed current quote" in main_post_recovery_digest.stdout
    assert "Regular quote selections" in main_post_recovery_digest.stdout
    assert "Matched image selections" in main_post_recovery_digest.stdout
    assert "Image cycle status" in main_post_recovery_digest.stdout
    assert "Quote cycle resets" in main_post_recovery_digest.stdout
    assert "t09.jpg" in main_post_recovery_digest.stdout
    assert "7.25" in main_post_recovery_digest.stdout
    assert "MAX_QUOTE_IMAGE_PAIR_ATTEMPTS=25" not in main_post_recovery_digest.stdout
    assert "IMAGE_STRONG_MISMATCH_PENALTY=-10000.0" not in main_post_recovery_digest.stdout
    assert "2 operational error(s)" not in main_post_recovery_digest.stdout

    regular_image_usage_base = prepare_base_dir(tmp_path / "digest-regular-image-usage")
    origin_hash = "a" * 64
    cross_hash = "b" * 64
    regular_image_usage_lines = [
        f"2026-07-03 12:00:{idx:02d} INFO     choose_matched_unused_image:4904 - REGULAR_IMAGE_SELECTED source=original basename=t{idx:02d}.jpg score=7.{idx} origin_quote_hash= origin_quote_match=false origin_quote_boost=0.0"
        for idx in range(1, 8)
    ]
    regular_image_usage_lines.extend(
        [
            f"2026-07-03 12:01:{idx:02d} INFO     choose_matched_unused_image:4904 - REGULAR_IMAGE_SELECTED source=generated basename=tg_{origin_hash}.png score=18.{idx} origin_quote_hash={origin_hash} origin_quote_match=true origin_quote_boost=4.0"
            for idx in range(1, 4)
        ]
    )
    regular_image_usage_lines.extend(
        [
            f"2026-07-03 12:02:{idx:02d} INFO     choose_matched_unused_image:4904 - REGULAR_IMAGE_SELECTED source=generated basename=tg_{cross_hash}.png score=16.{idx} origin_quote_hash={cross_hash} origin_quote_match=false origin_quote_boost=0.0"
            for idx in range(1, 3)
        ]
    )
    write_digest_log(regular_image_usage_base, regular_image_usage_lines)
    regular_image_usage_digest = run_digest(regular_image_usage_base)
    assert regular_image_usage_digest.returncode == 0, regular_image_usage_digest.stderr
    assert "## Regular image usage" in regular_image_usage_digest.stdout
    assert "selections          = 12" in regular_image_usage_digest.stdout
    assert "original_images     = 7" in regular_image_usage_digest.stdout
    assert "generated_images    = 5" in regular_image_usage_digest.stdout
    assert "originating_quote   = 3" in regular_image_usage_digest.stdout
    assert "cross_quote         = 2" in regular_image_usage_digest.stdout
    assert "generated_share     = 41.7%" in regular_image_usage_digest.stdout
    assert "origin_match_share  = 60.0%" in regular_image_usage_digest.stdout
    assert "Regular image selection metadata" in regular_image_usage_digest.stdout
    assert f"| 2026-07-03 12:01:01 | generated | tg_{origin_hash}.png | 18.1 | true | 4 |  |" in regular_image_usage_digest.stdout

    original_only_base = prepare_base_dir(tmp_path / "digest-regular-image-original-only")
    write_digest_log(
        original_only_base,
        [
            "2026-07-03 12:00:00 INFO     choose_matched_unused_image:4904 - REGULAR_IMAGE_SELECTED source=original basename=t44.jpg score=12.5 origin_quote_hash= origin_quote_match=false origin_quote_boost=0.0",
            "2026-07-03 12:00:01 INFO     choose_matched_unused_image:4904 - REGULAR_IMAGE_SELECTED source=original basename=t45.jpg score=11.0 origin_quote_hash= origin_quote_match=false origin_quote_boost=0.0",
        ],
    )
    original_only_digest = run_digest(original_only_base)
    assert original_only_digest.returncode == 0, original_only_digest.stderr
    assert "selections          = 2" in original_only_digest.stdout
    assert "original_images     = 2" in original_only_digest.stdout
    assert "generated_images    = 0" in original_only_digest.stdout
    assert "originating_quote   = 0" in original_only_digest.stdout
    assert "cross_quote         = 0" in original_only_digest.stdout

    generated_only_base = prepare_base_dir(tmp_path / "digest-regular-image-generated-only")
    write_digest_log(
        generated_only_base,
        [
            f"2026-07-03 12:00:00 INFO     choose_matched_unused_image:4904 - REGULAR_IMAGE_SELECTED source=generated basename=tg_{origin_hash}.png score=19.0 origin_quote_hash={origin_hash} origin_quote_match=true origin_quote_boost=4.0",
            f"2026-07-03 12:00:01 INFO     choose_matched_unused_image:4904 - REGULAR_IMAGE_SELECTED source=generated basename=tg_{cross_hash}.png score=14.0 origin_quote_hash={cross_hash} origin_quote_match=false origin_quote_boost=0.0",
        ],
    )
    generated_only_digest = run_digest(generated_only_base)
    assert generated_only_digest.returncode == 0, generated_only_digest.stderr
    assert "selections          = 2" in generated_only_digest.stdout
    assert "original_images     = 0" in generated_only_digest.stdout
    assert "generated_images    = 2" in generated_only_digest.stdout
    assert "originating_quote   = 1" in generated_only_digest.stdout
    assert "cross_quote         = 1" in generated_only_digest.stdout

    older_log_base = prepare_base_dir(tmp_path / "digest-regular-image-old-log")
    write_digest_log(
        older_log_base,
        [
            "2026-07-03 12:00:00 INFO     choose_matched_unused_image:4534 - Selected matched image basename=t09.jpg image_no=8 score=7.25 components={'topic': 3.0}",
        ],
    )
    older_log_digest = run_digest(older_log_base)
    assert older_log_digest.returncode == 0, older_log_digest.stderr
    assert "Matched image selections" in older_log_digest.stdout
    assert "Regular image usage" not in older_log_digest.stdout

    malformed_log_base = prepare_base_dir(tmp_path / "digest-regular-image-malformed")
    write_digest_log(
        malformed_log_base,
        [
            "2026-07-03 12:00:00 INFO     choose_matched_unused_image:4904 - REGULAR_IMAGE_SELECTED source=generated basename=tg_bad.png score=nope",
        ],
    )
    malformed_digest = run_digest(malformed_log_base)
    assert malformed_digest.returncode == 0, malformed_digest.stderr
    assert "Regular image usage" not in malformed_digest.stdout

    same_second_base = prepare_base_dir(tmp_path / "digest-same-second")
    same_second_state = same_second_base / ".digest_state.json"
    log_path = same_second_base / "test.log"
    log_path.write_text(
        "2026-07-03 10:00:00 ERROR    first:1 - First same-second error\n",
        encoding="utf-8",
    )
    first_same_second = run_digest(same_second_base, state_file=same_second_state)
    assert first_same_second.returncode == 0, first_same_second.stderr
    assert "First same-second error" in first_same_second.stdout
    with log_path.open("a", encoding="utf-8") as f:
        f.write("2026-07-03 10:00:00 ERROR    second:2 - Second same-second error\n")
    second_same_second = run_digest(same_second_base, state_file=same_second_state)
    assert second_same_second.returncode == 0, second_same_second.stderr
    assert "Second same-second error" in second_same_second.stdout
    assert "First same-second error" not in second_same_second.stdout
    third_same_second = run_digest(same_second_base, state_file=same_second_state)
    assert third_same_second.returncode == 0, third_same_second.stderr
    assert "First same-second error" not in third_same_second.stdout
    assert "Second same-second error" not in third_same_second.stdout
    assert "no matching records" in third_same_second.stdout
    with log_path.open("a", encoding="utf-8") as f:
        f.write("2026-07-03 10:00:00 ERROR    third:3 - Third same-second error\n")
    fourth_same_second = run_digest(same_second_base, state_file=same_second_state)
    assert fourth_same_second.returncode == 0, fourth_same_second.stderr
    assert "Third same-second error" in fourth_same_second.stdout
    assert "First same-second error" not in fourth_same_second.stdout
    assert "Second same-second error" not in fourth_same_second.stdout
    fifth_same_second = run_digest(same_second_base, state_file=same_second_state)
    assert fifth_same_second.returncode == 0, fifth_same_second.stderr
    assert "Third same-second error" not in fifth_same_second.stdout
    assert "no matching records" in fifth_same_second.stdout

    xai_cooldown_base = prepare_base_dir(tmp_path / "digest-xai-cooldown")
    (xai_cooldown_base / "test.log").write_text(
        "2026-07-03 12:00:00 DEBUG    save_state:994 - State being saved: "
        "{\"api_cooldown_until_epoch\": 0, "
        "\"xai_api_cooldown_until_epoch\": 4102444800, "
        "\"xai_api_cooldown_reason\": \"too many xai API errors in the last hour\", "
        "\"quote_api_cooldown_until_epoch\": 0}\n",
        encoding="utf-8",
    )
    write_json(
        xai_cooldown_base / "bot_state.json",
        {
            "daily_reply_count": 0,
            "api_cooldown_until_epoch": 0,
            "xai_api_cooldown_until_epoch": 4102444800,
            "xai_api_cooldown_reason": "too many xai API errors in the last hour",
            "quote_api_cooldown_until_epoch": 0,
        },
    )
    xai_cooldown_digest = run_digest(xai_cooldown_base)
    assert xai_cooldown_digest.returncode == 0, xai_cooldown_digest.stderr
    assert "xAI cooldown active now" in xai_cooldown_digest.stdout
    assert "no API cooldown" not in xai_cooldown_digest.stdout

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
    write_json(
        stale_base / "bot_state.json",
        {
            "daily_reply_count": 0,
            "api_cooldown_until_epoch": 1,
            "api_cooldown_reason": "old cooldown",
        },
    )
    stale_digest = run_digest(stale_base)
    assert stale_digest.returncode == 0, stale_digest.stderr
    assert "no API cooldown" in stale_digest.stdout
    assert "API cooldown occurred" not in stale_digest.stdout
    assert "x_read_api_cooldown_until = 1" in stale_digest.stdout
    assert "x_read_api_cooldown_reason = old cooldown" in stale_digest.stdout
    assert "expired" in stale_digest.stdout
    assert "mention_fetch_attempts         = 1" in stale_digest.stdout
    assert "mention_checks_skipped_spacing = 1" in stale_digest.stdout
    assert "Mention direct skips" in stale_digest.stdout
    assert "@MrsMThatcher @other" in stale_digest.stdout

    cleared_base = prepare_base_dir(tmp_path / "digest-cleared-cooldown")
    cleared_state_file = cleared_base / ".digest_state.json"
    write_json(
        cleared_state_file,
        {
            "last_log_entry_time": "2026-07-03 10:00:00",
            "last_known_latest_state": {
                "time": "2026-07-03 10:00:00",
                "api_cooldown_until_epoch": 1783054542,
                "api_cooldown_until_human": "2026-07-03 05:55:42",
                "api_cooldown_reason": "old cooldown",
                "quote_api_cooldown_until_epoch": 1783054542,
                "quote_api_cooldown_until_human": "2026-07-03 05:55:42",
                "quote_api_cooldown_reason": "old quote cooldown",
                "x_write_api_cooldown_until_epoch": 1783054542,
                "x_write_api_cooldown_until_human": "2026-07-03 05:55:42",
                "x_write_api_cooldown_reason": "old write cooldown",
            },
            "last_known_latest_config": {},
        },
    )
    (cleared_base / "test.log").write_text(
        "\n".join(
            [
                "2026-07-03 11:00:00 DEBUG    save_state:994 - State being saved: {\"api_cooldown_until_epoch\": 0, \"api_cooldown_reason\": \"\", \"quote_api_cooldown_until_epoch\": 0, \"quote_api_cooldown_reason\": \"\", \"x_write_api_cooldown_until_epoch\": 0, \"x_write_api_cooldown_reason\": \"\"}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        cleared_base / "bot_state.json",
        {
            "daily_reply_count": 0,
            "api_cooldown_until_epoch": 0,
            "api_cooldown_reason": "",
            "quote_api_cooldown_until_epoch": 0,
            "quote_api_cooldown_reason": "",
            "x_write_api_cooldown_until_epoch": 0,
            "x_write_api_cooldown_reason": "",
        },
    )
    cleared_digest = run_digest(cleared_base, state_file=cleared_state_file)
    assert cleared_digest.returncode == 0, cleared_digest.stderr
    assert "x_read_api_cooldown_until = 0  none" in cleared_digest.stdout
    assert "x_write_api_cooldown_until = 0  none" in cleared_digest.stdout
    assert "quote_api_cooldown_until = 0  none" in cleared_digest.stdout
    assert "2026-07-03 05:55:42" not in cleared_digest.stdout
    assert "old cooldown" not in cleared_digest.stdout
    assert "old write cooldown" not in cleared_digest.stdout


def test_digest_latest_state_counts_do_not_default_missing_lists_to_zero(tmp_path: Path) -> None:
    present_base = tmp_path / "digest-present-meme-count"
    meme_names = [f"{idx:03d}_meme.png" for idx in range(20)]
    write_digest_log(
        present_base,
        [
            "2026-07-06 15:21:25 DEBUG    save_state:1632 - State being saved: "
            + json.dumps({"posted_meme_filenames": meme_names}),
        ],
    )
    write_json(
        present_base / "bot_state.json",
        {"daily_reply_count": 1, "posted_meme_filenames": meme_names},
    )
    present_digest = run_digest(present_base)
    assert present_digest.returncode == 0, present_digest.stderr
    assert "posted_meme_count       = 20" in present_digest.stdout

    empty_base = tmp_path / "digest-empty-meme-count"
    write_digest_log(
        empty_base,
        [
            '2026-07-06 15:21:25 DEBUG    save_state:1632 - State being saved: {"posted_meme_filenames": []}',
        ],
    )
    write_json(
        empty_base / "bot_state.json",
        {"daily_reply_count": 1, "posted_meme_filenames": []},
    )
    empty_digest = run_digest(empty_base)
    assert empty_digest.returncode == 0, empty_digest.stderr
    assert "posted_meme_count       = 0" in empty_digest.stdout

    absent_base = tmp_path / "digest-absent-meme-count"
    write_digest_log(
        absent_base,
        [
            '2026-07-06 15:21:25 DEBUG    save_state:1632 - State being saved: {"daily_reply_count": 1}',
        ],
    )
    write_json(absent_base / "bot_state.json", {"daily_reply_count": 1})
    absent_digest = run_digest(absent_base)
    assert absent_digest.returncode == 0, absent_digest.stderr
    assert "posted_meme_count       = unknown (not present in latest snapshot)" in absent_digest.stdout
    assert "posted_meme_count       = 0" not in absent_digest.stdout


def test_digest_can_use_bot_state_for_authoritative_current_state_metrics(tmp_path: Path) -> None:
    base = tmp_path / "digest-authoritative-state"
    meme_names = [f"{idx:03d}_meme.png" for idx in range(20)]
    state_ts = datetime(2026, 7, 6, 15, 21, 25).timestamp()
    write_digest_log(
        base,
        [
            '2026-07-06 15:21:25 DEBUG    save_state:1632 - State being saved: {"daily_reply_count": 1}',
        ],
    )
    state_path = base / "bot_state.json"
    write_json(state_path, {"posted_meme_filenames": meme_names, "daily_reply_count": 2})
    os.utime(state_path, (state_ts, state_ts))
    digest = run_digest(base)
    assert digest.returncode == 0, digest.stderr
    assert "authoritative current state" in digest.stdout
    assert "posted_meme_count       = 20" in digest.stdout
    assert "posted_meme_count       = unknown" not in digest.stdout


def test_digest_latest_state_uses_current_runtime_state_even_after_log_window_end(tmp_path: Path) -> None:
    base = tmp_path / "digest-latest-state-window-boundary"
    window_end = "2026-07-08 06:39:38"
    future_state_ts = datetime(2026, 7, 8, 6, 39, 45).timestamp()
    write_digest_log(
        base,
        [
            "2026-07-08 05:39:31 INFO maybe_reply_to_quote_tweets:4000 - Considering quote tweet id=900 author_id=777 original_post_id=555 text='Quote text'",
            "2026-07-08 05:39:32 INFO maybe_reply_to_quote_tweets:4020 - Generated reply to quote tweet 900: 'A quote reply.'",
            "2026-07-08 05:39:33 INFO create_post:2500 - Created X post successfully response={'data': {'id': '901', 'text': 'A quote reply.'}}",
            "2026-07-08 05:39:34 INFO maybe_reply_to_quote_tweets:4100 - Quote-tweet reply posted successfully",
            '2026-07-08 05:39:35 DEBUG save_state:1632 - State being saved: {"daily_reply_count": 1, "daily_quote_reply_count": 1, "last_seen_mention_id": "800", "next_reply_lane_priority": "normal"}',
            "2026-07-08 06:39:38 INFO main:6000 - Main loop sleeping",
            "2026-07-08 06:39:45 INFO maybe_reply_to_mentions:3000 - Considering mention id=1000 author_id=456 text='@MrsMThatcher hello'",
            "2026-07-08 06:39:45 INFO maybe_reply_to_mentions:3050 - Generated reply to mention 1000: 'A mention reply.'",
            "2026-07-08 06:39:45 INFO maybe_reply_to_mentions:3100 - Reply posted successfully",
            '2026-07-08 06:39:45 DEBUG save_state:1632 - State being saved: {"daily_reply_count": 2, "daily_quote_reply_count": 1, "last_seen_mention_id": "1000", "next_reply_lane_priority": "quote"}',
        ],
    )
    state_path = base / "bot_state.json"
    write_json(
        state_path,
        {
            "daily_reply_count": 2,
            "daily_quote_reply_count": 1,
            "last_seen_mention_id": "1000",
            "next_reply_lane_priority": "quote",
        },
    )
    os.utime(state_path, (future_state_ts, future_state_ts))

    digest = run_digest(base, until=window_end)
    assert digest.returncode == 0, digest.stderr
    assert (
        "Observed event window: `2026-07-08 05:39:31` → `2026-07-08 06:39:38`"
        in digest.stdout
    )
    assert "0 mention replies" in digest.stdout
    assert "1 quote-tweet reply" in digest.stdout
    assert "A mention reply." not in digest.stdout
    assert "State timestamp: `2026-07-08 06:39:45` (authoritative current state" in digest.stdout
    assert "daily_reply_count       = 2" in digest.stdout
    assert "daily_quote_reply_count = 1" in digest.stdout
    assert "last_seen_mention_id    = 1000" in digest.stdout


def test_digest_without_until_still_uses_current_runtime_state(tmp_path: Path) -> None:
    base = tmp_path / "digest-latest-state-implicit-window-boundary"
    future_state_ts = datetime(2026, 7, 8, 6, 39, 45).timestamp()
    write_digest_log(
        base,
        [
            '2026-07-08 05:39:35 DEBUG save_state:1632 - State being saved: {"daily_reply_count": 1, "daily_quote_reply_count": 1, "last_seen_mention_id": "800", "next_reply_lane_priority": "normal"}',
            "2026-07-08 06:39:38 INFO main:6000 - Main loop sleeping",
        ],
    )
    state_path = base / "bot_state.json"
    write_json(
        state_path,
        {
            "daily_reply_count": 2,
            "daily_quote_reply_count": 1,
            "last_seen_mention_id": "1000",
            "next_reply_lane_priority": "quote",
        },
    )
    os.utime(state_path, (future_state_ts, future_state_ts))

    digest = run_digest(base)
    assert digest.returncode == 0, digest.stderr
    assert (
        "Observed event window: `2026-07-08 05:39:35` → `2026-07-08 06:39:38`"
        in digest.stdout
    )
    assert "State timestamp: `2026-07-08 06:39:45` (authoritative current state" in digest.stdout
    assert "daily_reply_count       = 2" in digest.stdout
    assert "daily_quote_reply_count = 1" in digest.stdout
    assert "last_seen_mention_id    = 1000" in digest.stdout


def test_digest_authoritative_state_at_window_end_is_eligible(tmp_path: Path) -> None:
    base = tmp_path / "digest-latest-state-window-end-eligible"
    window_end = "2026-07-08 06:39:38"
    state_ts = datetime(2026, 7, 8, 6, 39, 38).timestamp()
    write_digest_log(
        base,
        [
            '2026-07-08 06:39:35 DEBUG save_state:1632 - State being saved: {"daily_reply_count": 1, "daily_quote_reply_count": 1}',
            "2026-07-08 06:39:38 INFO main:6000 - Main loop sleeping",
        ],
    )
    state_path = base / "bot_state.json"
    write_json(
        state_path,
        {
            "daily_reply_count": 2,
            "daily_quote_reply_count": 1,
            "last_seen_mention_id": "1000",
        },
    )
    os.utime(state_path, (state_ts, state_ts))

    digest = run_digest(base, until=window_end)
    assert digest.returncode == 0, digest.stderr
    assert "State timestamp: `2026-07-08 06:39:38` (authoritative current state" in digest.stdout
    assert "daily_reply_count       = 2" in digest.stdout
    assert "last_seen_mention_id    = 1000" in digest.stdout


def test_digest_resume_state_never_overrides_current_runtime_state(tmp_path: Path) -> None:
    base = tmp_path / "digest-saved-state-window-boundary"
    state_file = tmp_path / "digest-state.json"
    window_end = "2026-07-08 06:39:38"
    future_state_ts = datetime(2026, 7, 8, 6, 39, 45).timestamp()
    write_digest_log(
        base,
        [
            "2026-07-08 06:39:38 INFO main:6000 - Main loop sleeping",
        ],
    )
    write_json(
        state_file,
        {
            "last_log_entry_time": "2026-07-08 06:00:00",
            "last_known_latest_state": {
                "time": "2026-07-08 05:39:35",
                "daily_reply_count": 1,
                "daily_quote_reply_count": 1,
                "last_seen_mention_id": "800",
            },
            "last_known_latest_config": {},
        },
    )
    state_path = base / "bot_state.json"
    write_json(
        state_path,
        {
            "daily_reply_count": 2,
            "daily_quote_reply_count": 1,
            "last_seen_mention_id": "1000",
        },
    )
    os.utime(state_path, (future_state_ts, future_state_ts))

    digest = run_digest(base, state_file=state_file, until=window_end)
    assert digest.returncode == 0, digest.stderr
    assert "## Latest state (stale carried-forward snapshot)" not in digest.stdout
    assert "State timestamp: `2026-07-08 06:39:45` (authoritative current state" in digest.stdout
    assert "daily_reply_count       = 2" in digest.stdout
    assert "daily_quote_reply_count = 1" in digest.stdout
    assert "last_seen_mention_id    = 1000" in digest.stdout
    assert "snapshot_daily_reply_count" not in digest.stdout
    assert "snapshot_last_seen_mention_id" not in digest.stdout


def test_digest_saved_state_backfill_rejects_future_saved_state_snapshot(tmp_path: Path) -> None:
    base = tmp_path / "digest-saved-future-state-window-boundary"
    state_file = tmp_path / "future-digest-state.json"
    window_end = "2026-07-08 06:39:38"
    write_digest_log(
        base,
        [
            "2026-07-08 06:39:38 INFO main:6000 - Main loop sleeping",
        ],
    )
    write_json(
        state_file,
        {
            "last_log_entry_time": "2026-07-08 06:00:00",
            "last_known_latest_state": {
                "time": "2026-07-08 06:39:45",
                "daily_reply_count": 2,
                "daily_quote_reply_count": 1,
                "last_seen_mention_id": "1000",
            },
            "last_known_latest_config": {},
        },
    )

    digest = run_digest(base, state_file=state_file, until=window_end)
    assert digest.returncode == 0, digest.stderr
    assert "daily_reply_count       = 2" not in digest.stdout
    assert "last_seen_mention_id    = 1000" not in digest.stdout


def test_digest_groups_handled_media_v2_fallback_as_one_warning(tmp_path: Path) -> None:
    base = tmp_path / "digest-media-fallback-handled"
    media_file = "/tmp/016_impact18_share17_gradeA_post_as_is_Image150.png"
    write_digest_log(
        base,
        [
            f"2026-07-06 15:20:24 INFO     post_next_meme:5180 - Posting meme image: {media_file}",
            f"2026-07-06 15:20:24 INFO     upload_media_v2:2970 - Uploading media via X API v2: {media_file}",
            "2026-07-06 15:21:24 ERROR    x_request:1967 - X request failed before receiving response\nrequests.exceptions.ReadTimeout: HTTPSConnectionPool(host='api.x.com', port=443): Read timed out. (read timeout=60.0)",
            "2026-07-06 15:21:24 ERROR    upload_media:3054 - v2 media upload failed; trying v1.1 fallback\nApiError: HTTPSConnectionPool(host='api.x.com', port=443): Read timed out. (read timeout=60.0)",
            f"2026-07-06 15:21:24 INFO     upload_media_v1_1:3000 - Uploading media via legacy v1.1 fallback: {media_file}",
            "2026-07-06 15:21:25 INFO     upload_media_v1_1:3043 - Uploaded media via v1.1. media_id=2074136638502866945",
            "2026-07-06 15:21:25 INFO     create_post:3113 - Created X post successfully. response={'data': {'text': 'https://t.co/AvJDHwMiV6', 'id': '2074136641040499171'}}",
            '2026-07-06 15:21:25 INFO     log_event:330 - EVENT {"event":"main_post_posted","filename":"016_impact18_share17_gradeA_post_as_is_Image150.png","lane":"daily_meme","post_id":"2074136641040499171"}',
            "2026-07-06 15:21:25 INFO     post_next_meme:5281 - Daily meme posted successfully. posted_id=2074136641040499171 file=016_impact18_share17_gradeA_post_as_is_Image150.png",
        ],
    )
    digest = run_digest(base)
    assert digest.returncode == 0, digest.stderr
    assert "1 handled media-upload fallback" in digest.stdout
    assert "2 current independent errors" not in digest.stdout
    assert "current health: no unresolved operational incidents" in digest.stdout
    assert "Media upload incidents" in digest.stdout
    assert "handled_fallbacks     = 1" in digest.stdout
    assert "unrecovered_failures  = 0" in digest.stdout
    assert "v2 upload failed; v1.1 fallback succeeded and final post completed" in digest.stdout
    assert "Read timed out. (read timeout=60.0)" in digest.stdout


def test_digest_counts_unrecovered_media_fallback_as_one_incident(tmp_path: Path) -> None:
    base = tmp_path / "digest-media-fallback-unrecovered"
    media_file = "/tmp/meme.png"
    write_digest_log(
        base,
        [
            f"2026-07-06 15:20:24 INFO     upload_media_v2:2970 - Uploading media via X API v2: {media_file}",
            "2026-07-06 15:21:24 ERROR    x_request:1967 - X request failed before receiving response\nrequests.exceptions.ReadTimeout: timed out",
            "2026-07-06 15:21:24 ERROR    upload_media:3054 - v2 media upload failed; trying v1.1 fallback",
            "2026-07-06 15:21:25 ERROR    upload_media_v1_1:3043 - v1.1 media upload failed: configured failure",
        ],
    )
    digest = run_digest(base)
    assert digest.returncode == 0, digest.stderr
    assert "1 unrecovered media-upload failure" in digest.stdout
    assert "handled_fallbacks     = 0" in digest.stdout
    assert "unrecovered_failures  = 1" in digest.stdout
    assert "2 operational error(s)" not in digest.stdout
    assert "Media upload incidents" in digest.stdout


def test_digest_keeps_unrelated_nearby_errors_separate(tmp_path: Path) -> None:
    base = tmp_path / "digest-unrelated-errors"
    write_digest_log(
        base,
        [
            "2026-07-06 15:21:24 ERROR    first:1 - First unrelated failure",
            "2026-07-06 15:21:25 ERROR    second:2 - Second unrelated failure",
        ],
    )
    digest = run_digest(base)
    assert digest.returncode == 0, digest.stderr
    assert "current health: 2 unresolved operational incidents" in digest.stdout
    assert "First unrelated failure" in digest.stdout
    assert "Second unrelated failure" in digest.stdout


def test_digest_does_not_double_count_repeated_media_chain_lines(tmp_path: Path) -> None:
    base = tmp_path / "digest-media-repeated-chain"
    media_file = "/tmp/meme.png"
    write_digest_log(
        base,
        [
            f"2026-07-06 15:20:24 INFO     upload_media_v2:2970 - Uploading media via X API v2: {media_file}",
            "2026-07-06 15:21:24 ERROR    x_request:1967 - X request failed before receiving response\nrequests.exceptions.ReadTimeout: timed out",
            "2026-07-06 15:21:24 ERROR    x_request:1968 - X request failed before receiving response\nrequests.exceptions.ReadTimeout: timed out",
            "2026-07-06 15:21:24 ERROR    upload_media:3054 - v2 media upload failed; trying v1.1 fallback",
            "2026-07-06 15:21:25 INFO     upload_media_v1_1:3043 - Uploaded media via v1.1. media_id=2074136638502866945",
            "2026-07-06 15:21:25 INFO     post_random_quote:5281 - Quote/image posted successfully. posted_id=2074136641040499171",
        ],
    )
    digest = run_digest(base)
    assert digest.returncode == 0, digest.stderr
    assert "1 handled media-upload fallback" in digest.stdout
    assert "operational error(s)" not in digest.stdout


def test_digest_reports_confirmed_reply_receipt_lifecycle(tmp_path: Path) -> None:
    base = tmp_path / "digest-confirmed-reply-recovery"
    write_digest_log(
        base,
        [
            "2026-07-07 05:48:25 WARNING  write_confirmed_reply_receipt:5614 - Wrote confirmed reply receipt pending local reconciliation source=mention target_id=123 reply_post_id=999 path=/tmp/confirmed_reply_receipt.json",
            "2026-07-07 06:02:11 WARNING  reconcile_confirmed_reply_receipt:5690 - Reconciling confirmed reply receipt source=mention target_id=123 reply_post_id=999",
            "2026-07-07 06:02:11 INFO     remove_confirmed_reply_receipt:5621 - Removed reconciled confirmed-reply receipt source=mention target_id=123 reply_post_id=999 path=/tmp/confirmed_reply_receipt.json",
        ],
    )

    digest = run_digest(base)

    assert digest.returncode == 0, digest.stderr
    assert "## Confirmed-reply receipt lifecycle" in digest.stdout
    assert "## Confirmed-reply recovery" not in digest.stdout
    assert (
        "Routine confirmed-reply receipt write/remove pairs completed: **1**."
        in digest.stdout
    )
    assert "Stale or unresolved confirmed-reply receipts:" not in digest.stdout
    assert "operational error(s)" not in digest.stdout


def test_digest_reports_pending_confirmed_reply_receipt(tmp_path: Path) -> None:
    base = tmp_path / "digest-confirmed-reply-pending"
    write_digest_log(
        base,
        [
            "2026-07-07 05:48:25 WARNING  write_confirmed_reply_receipt:5614 - Wrote confirmed reply receipt pending local reconciliation: /tmp/confirmed_reply_receipt.json",
        ],
    )

    digest = run_digest(base)

    assert digest.returncode == 0, digest.stderr
    assert "## Confirmed-reply recovery" in digest.stdout
    assert "written" in digest.stdout
    assert "operational error(s)" not in digest.stdout


def test_digest_reports_confirmed_reply_recovery_failures(tmp_path: Path) -> None:
    base = tmp_path / "digest-confirmed-reply-failures"
    write_digest_log(
        base,
        [
            "2026-07-07 06:02:11 WARNING  reconcile_confirmed_reply_receipt:5690 - Reconciling confirmed reply receipt target_id=123 reply_post_id=999",
            "2026-07-07 06:02:12 CRITICAL reconcile_confirmed_reply_receipt:5700 - Confirmed reply receipt was applied in memory but state save failed; receipt remains for retry",
            "2026-07-07 06:03:12 CRITICAL reconcile_confirmed_reply_receipt:5708 - Confirmed reply receipt state was saved but receipt removal failed",
            "2026-07-07 06:04:12 CRITICAL load_confirmed_reply_receipt:5580 - Malformed confirmed-reply receipt blocks auto-reply processing until repaired: /tmp/confirmed_reply_receipt.json",
            "2026-07-07 06:05:12 CRITICAL load_confirmed_reply_receipt:5589 - Invalid confirmed-reply receipt blocks auto-reply processing until repaired: /tmp/confirmed_reply_receipt.json",
        ],
    )

    digest = run_digest(base)

    assert digest.returncode == 0, digest.stderr
    assert "4 confirmed-reply recovery records in window" in digest.stdout
    assert "Confirmed replies with local recovery/persistence trouble" in digest.stdout
    assert "state save failed" in digest.stdout
    assert "receipt removal failed" in digest.stdout
    assert "Malformed confirmed-reply receipt" in digest.stdout
    assert "Invalid confirmed-reply receipt" in digest.stdout


def test_digest_reports_confirmed_reply_emergency_persistence_paths(tmp_path: Path) -> None:
    base = tmp_path / "digest-confirmed-reply-emergency"
    write_digest_log(
        base,
        [
            "2026-07-07 06:10:00 CRITICAL maybe_reply_to_mentions:5980 - Confirmed reply id=999 to target=123 but failed writing recovery receipt; attempting direct durable state save",
            "2026-07-07 06:11:00 CRITICAL maybe_reply_to_mentions:5990 - Confirmed reply id=1000 to target=124 but both receipt write and emergency state save failed",
        ],
    )

    digest = run_digest(base)

    assert digest.returncode == 0, digest.stderr
    assert "2 confirmed-reply recovery records in window" in digest.stdout
    assert "failed writing recovery receipt" in digest.stdout
    assert "both receipt write and emergency state save failed" in digest.stdout


def test_digest_reports_reply_media_context(tmp_path: Path) -> None:
    base = tmp_path / "digest-reply-media-context"
    write_digest_log(
        base,
        [
            "2026-07-07 07:00:00 INFO     reply_media_context_for_candidate:2350 - Reply media context lane=mention target_id=123 photos=1 mode=multimodal status=supplied",
            "2026-07-07 07:00:01 WARNING  ask_grok_for_reply:5647 - Reply media context fallback lane=mention target_id=123 photos_expected=1 initial_mode=multimodal final_mode=text_fallback status=unavailable http_status=400",
            "2026-07-07 07:01:00 WARNING  reply_media_context_for_candidate:2365 - Reply media context unavailable lane=quote_tweet target_id=456 photos_expected=1 mode=multimodal status=unavailable",
        ],
    )

    digest = run_digest(base)

    assert digest.returncode == 0, digest.stderr
    assert "## Reply media context" in digest.stdout
    assert "| time | level | lane | target_id | photos | mode | status | http_status |" in digest.stdout
    assert "| 2026-07-07 07:00:00 | INFO | mention | 123 | 1 | multimodal | supplied |  |" in digest.stdout
    assert "| 2026-07-07 07:00:01 | WARNING | mention | 123 | 1 | text_fallback | unavailable | 400 |" in digest.stdout
    assert "| 2026-07-07 07:01:00 | WARNING | quote_tweet | 456 | 1 | multimodal | unavailable |  |" in digest.stdout
    assert "operational error(s)" not in digest.stdout


def test_digest_reports_regular_image_made_with_ai_from_create_post(tmp_path: Path) -> None:
    base = tmp_path / "digest-regular-image-made-with-ai"
    origin_hash = "a" * 64
    quote_hash = "b" * 64
    write_digest_log(
        base,
        [
            f"2026-07-08 12:00:00 INFO     select_quote_candidate:4630 - Selected quote line_no=1 quote_hash={quote_hash} weight=1.00 seasonal_boost=False",
            "2026-07-08 12:00:01 INFO     choose_matched_unused_image:4931 - Selected matched image basename=t01.jpg image_no=0 score=7.00 components=historical=0.0",
            "2026-07-08 12:00:02 INFO     log_regular_image_selection:4792 - REGULAR_IMAGE_SELECTED source=original basename=t01.jpg score=7.0 origin_quote_hash= origin_quote_match=false origin_quote_boost=0.0",
            "2026-07-08 12:00:03 INFO     create_post:3204 - Creating X post. reply_to_id=None media_count=1 made_with_ai=False text='Original quote.'",
            "2026-07-08 12:00:04 INFO     post_random_quote:5198 - Quote/image posted successfully. posted_id=1001",
            f"2026-07-08 12:01:00 INFO     select_quote_candidate:4630 - Selected quote line_no=2 quote_hash={quote_hash} weight=1.00 seasonal_boost=False",
            f"2026-07-08 12:01:01 INFO     choose_matched_unused_image:4931 - Selected matched image basename=tg_{origin_hash}.png image_no=1 score=12.00 components=generated_origin_quote=4.0",
            f"2026-07-08 12:01:02 INFO     log_regular_image_selection:4792 - REGULAR_IMAGE_SELECTED source=generated basename=tg_{origin_hash}.png score=12.0 origin_quote_hash={origin_hash} origin_quote_match=false origin_quote_boost=0.0",
            "2026-07-08 12:01:03 INFO     create_post:3204 - Creating X post. reply_to_id=None media_count=1 made_with_ai=True text='Generated quote.'",
            "2026-07-08 12:01:04 INFO     post_random_quote:5198 - Quote/image posted successfully. posted_id=1002",
        ],
    )

    digest = run_digest(base)

    assert digest.returncode == 0, digest.stderr
    assert "made_with_ai_true   = 1" in digest.stdout
    assert "made_with_ai_false  = 1" in digest.stdout
    assert "made_with_ai_unknown = 0" in digest.stdout
    assert "| 2026-07-08 12:00:02 | original | t01.jpg | 7 | false | 0 | false |" in digest.stdout
    assert f"| 2026-07-08 12:01:02 | generated | tg_{origin_hash}.png | 12 | false | 0 | true |" in digest.stdout
    assert "| 2026-07-08 12:00:04 | 1001 | 1 |" in digest.stdout
    assert "| t01.jpg | 0 | 7.00 | false |" in digest.stdout
    assert "| 2026-07-08 12:01:04 | 1002 | 2 |" in digest.stdout
    assert f"| tg_{origin_hash}.png | 1 | 12.00 | true |" in digest.stdout


def test_digest_reports_generated_image_spacing_status(tmp_path: Path) -> None:
    base = tmp_path / "digest-generated-image-spacing"
    write_digest_log(
        base,
        [
            "2026-07-08 12:00:00 INFO     choose_regular_quote_image_pair:4984 - GENERATED_IMAGE_SPACING_STATUS pool_enabled=true allowed=false original_posts_since_generated=0 required=2",
            "2026-07-08 12:00:00 INFO     choose_regular_quote_image_pair:4984 - GENERATED_IMAGE_POOL_BLOCKED_BY_SPACING original_posts_since_generated=0 required=2",
            "2026-07-08 12:10:00 INFO     choose_regular_quote_image_pair:4984 - GENERATED_IMAGE_SPACING_STATUS pool_enabled=true allowed=true original_posts_since_generated=2 required=2",
            "2026-07-08 12:10:30 INFO     update_regular_generated_image_spacing_state:4880 - GENERATED_IMAGE_SPACING_STATE_UPDATED pool_enabled=true allowed=false original_posts_since_generated=0 required=2 image_source=generated image=tg_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png",
        ],
    )

    digest = run_digest(base)

    assert digest.returncode == 0, digest.stderr
    assert "## Generated image spacing" in digest.stdout
    assert "required_original_posts_between = 2" in digest.stdout
    assert "original_posts_since_generated  = 0" in digest.stdout
    assert "generated_pool_enabled          = true" in digest.stdout
    assert "generated_pool_allowed          = false" in digest.stdout
    assert "| 2026-07-08 12:00:00 | blocked |  |  | 0 | 2 |" in digest.stdout
    assert "| 2026-07-08 12:10:30 | state_updated | true | false | 0 | 2 |" in digest.stdout


@pytest.mark.parametrize(
    ("before_count", "after_count", "after_allowed", "image_source", "image_name"),
    [
        (2, 0, "false", "generated", "tg_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png"),
        (0, 1, "false", "original", "t01.jpg"),
        (1, 2, "true", "original", "t02.jpg"),
    ],
)
def test_digest_generated_spacing_latest_uses_post_transition_state(
    tmp_path: Path,
    before_count: int,
    after_count: int,
    after_allowed: str,
    image_source: str,
    image_name: str,
) -> None:
    base = tmp_path / f"digest-generated-image-spacing-transition-{before_count}-{after_count}"
    before_allowed = "true" if before_count >= 2 else "false"
    write_digest_log(
        base,
        [
            f"2026-07-08 12:00:00 INFO     choose_regular_quote_image_pair:4984 - GENERATED_IMAGE_SPACING_STATUS pool_enabled=true allowed={before_allowed} original_posts_since_generated={before_count} required=2",
            f"2026-07-08 12:00:10 INFO     update_regular_generated_image_spacing_state:4880 - GENERATED_IMAGE_SPACING_STATE_UPDATED pool_enabled=true allowed={after_allowed} original_posts_since_generated={after_count} required=2 image_source={image_source} image={image_name}",
        ],
    )

    digest = run_digest(base)

    assert digest.returncode == 0, digest.stderr
    assert f"original_posts_since_generated  = {after_count}" in digest.stdout
    assert f"generated_pool_allowed          = {after_allowed}" in digest.stdout


def test_digest_generated_spacing_resume_carries_latest_state(tmp_path: Path) -> None:
    base = tmp_path / "digest-generated-spacing-resume"
    state_file = tmp_path / "digest-state.json"
    write_digest_log(
        base,
        [
            "2026-07-08 12:00:00 INFO     update_regular_generated_image_spacing_state:4880 - GENERATED_IMAGE_SPACING_STATE_UPDATED pool_enabled=true allowed=false original_posts_since_generated=0 required=2 image_source=generated image=tg_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png",
        ],
    )

    first_digest = run_digest(base, state_file=state_file)

    assert first_digest.returncode == 0, first_digest.stderr
    assert "original_posts_since_generated  = 0" in first_digest.stdout
    assert "generated_pool_allowed          = false" in first_digest.stdout

    write_digest_log(
        base,
        [
            "2026-07-08 12:00:00 INFO     update_regular_generated_image_spacing_state:4880 - GENERATED_IMAGE_SPACING_STATE_UPDATED pool_enabled=true allowed=false original_posts_since_generated=0 required=2 image_source=generated image=tg_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png",
            "2026-07-08 12:05:00 INFO     main:9999 - heartbeat with no spacing event",
        ],
    )
    second_digest = run_digest(base, state_file=state_file)

    assert second_digest.returncode == 0, second_digest.stderr
    assert "original_posts_since_generated  = 0" in second_digest.stdout
    assert "generated_pool_allowed          = false" in second_digest.stdout

    write_digest_log(
        base,
        [
            "2026-07-08 12:00:00 INFO     update_regular_generated_image_spacing_state:4880 - GENERATED_IMAGE_SPACING_STATE_UPDATED pool_enabled=true allowed=false original_posts_since_generated=0 required=2 image_source=generated image=tg_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png",
            "2026-07-08 12:05:00 INFO     main:9999 - heartbeat with no spacing event",
            "2026-07-08 12:10:00 INFO     update_regular_generated_image_spacing_state:4880 - GENERATED_IMAGE_SPACING_STATE_UPDATED pool_enabled=true allowed=true original_posts_since_generated=2 required=2 image_source=original image=t02.jpg",
        ],
    )
    third_digest = run_digest(base, state_file=state_file)

    assert third_digest.returncode == 0, third_digest.stderr
    assert "original_posts_since_generated  = 2" in third_digest.stdout
    assert "generated_pool_allowed          = true" in third_digest.stdout


def test_digest_reports_xai_usage_events_and_totals(tmp_path: Path) -> None:
    base = tmp_path / "digest-xai-usage"
    write_digest_log(
        base,
        [
            "2026-07-06 15:46:26 INFO maybe_reply_to_mentions - Considering mention id=123 author_id=456 text='@MrsMThatcher hello'",
            "2026-07-06 15:46:27 INFO ask_grok_for_reply - Asking Grok for reply. context_text='Incoming post/comment to answer:\\n@MrsMThatcher hello'",
            "2026-07-06 15:46:37 INFO ask_grok_for_reply - xAI usage={'prompt_tokens': 533, 'completion_tokens': 20, 'total_tokens': 1314, 'prompt_tokens_details': {'text_tokens': 533, 'audio_tokens': 0, 'image_tokens': 0, 'cached_tokens': 128}, 'completion_tokens_details': {'reasoning_tokens': 761, 'audio_tokens': 0, 'accepted_prediction_tokens': 0, 'rejected_prediction_tokens': 0}, 'num_sources_used': 0, 'cost_in_usd_ticks': 24843500}",
            "2026-07-06 15:46:37 INFO maybe_reply_to_mentions - Generated reply to mention 123: 'A reply.'",
            "2026-07-06 15:47:00 INFO ask_grok_for_reply - xAI usage={'prompt_tokens': 539, 'completion_tokens': 18, 'total_tokens': 1109, 'prompt_tokens_details': {'cached_tokens': 128}, 'completion_tokens_details': {'reasoning_tokens': 552}, 'num_sources_used': 2, 'cost_in_usd_ticks': 19643500}",
        ],
    )

    digest = run_digest(base)
    assert digest.returncode == 0, digest.stderr
    assert "## xAI usage" in digest.stdout
    assert "| 2026-07-06 15:46:37 | mention | 123 | 533 | 128 | 761 | 20 | 1314 | 0 | 24843500 |" in digest.stdout
    assert "| 2026-07-06 15:47:00 | unknown |  | 539 | 128 | 552 | 18 | 1109 | 2 | 19643500 |" in digest.stdout
    assert "successful_xai_calls = 2" in digest.stdout
    assert "prompt_tokens        = 1072" in digest.stdout
    assert "cached_tokens        = 256" in digest.stdout
    assert "image_tokens         = 0" in digest.stdout
    assert "reasoning_tokens     = 1313" in digest.stdout
    assert "completion_tokens    = 38" in digest.stdout
    assert "total_tokens         = 2423" in digest.stdout
    assert "sources_used         = 2" in digest.stdout
    assert "known_cost_ticks_lower_bound = 44487000" in digest.stdout
    assert "0 mention replies" in digest.stdout


def test_digest_reports_reply_strategy_decisions(tmp_path: Path) -> None:
    base = tmp_path / "digest-reply-strategy"
    write_digest_log(base, [
        '2026-07-14 12:00:00 INFO log_event:420 - EVENT {"event":"reply_strategy_decision","mode":"historical_context","humour_tone":"dry","evidence_confidence":"medium","retrieved_quote_ids":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],"factual_claim_made":true,"grounded":true,"no_reply_reason":""}',
    ])
    digest = run_digest(base)
    assert digest.returncode == 0, digest.stderr
    assert "## Reply strategy decisions" in digest.stdout
    assert "| time | lane | strategy_version | mode | reply_requirement | route_source | tone | evidence_confidence | trusted_facts_supplied_count | used_fact_count | factual_claim | grounded | reviewer_verdict | model_call_count | revision_count | no_reply_reason |" in digest.stdout
    assert "| 2026-07-14 12:00:00 | unavailable |  | historical_context |  |  | dry | medium |  |  | True | True |  |  |  |  |" in digest.stdout


def test_digest_markdown_distinguishes_principle_and_editorial_no_reply_categories(
    tmp_path: Path,
) -> None:
    base = tmp_path / "digest-principle-strategy"
    write_digest_log(base, [
        '2026-07-14 12:00:00 INFO log_event:420 - EVENT {"event":"reply_strategy_decision","target_id":"1","lane":"mention","mode":"principle_reply","humour_tone":"none","evidence_confidence":"none","retrieved_quote_ids":[],"factual_claim_made":false,"grounded":false,"no_reply_reason":""}',
        '2026-07-14 12:00:01 INFO log_event:420 - EVENT {"event":"reply_strategy_outcome","status":"confirmed","target_id":"1","reply_post_id":"9","lane":"mention","mode":"principle_reply","humour_tone":"none","evidence_confidence":"none","retrieved_quote_ids":[],"factual_claim_made":false,"grounded":false,"no_reply_reason":""}',
        '2026-07-14 12:01:00 INFO log_event:420 - EVENT {"event":"reply_strategy_decision","target_id":"2","lane":"quote_tweet","mode":"no_reply","humour_tone":"none","evidence_confidence":"none","retrieved_quote_ids":[],"factual_claim_made":false,"grounded":false,"no_reply_reason":"no_reply_due_to_unverifiable_claim"}',
    ])

    result = run_digest(base)

    assert result.returncode == 0, result.stderr
    assert "principle_reply=1" in result.stdout
    assert "No-reply categories: no_reply_due_to_unverifiable_claim=1" in result.stdout


def test_digest_reports_xai_usage_unknown_context_and_malformed_records(tmp_path: Path) -> None:
    base = tmp_path / "digest-xai-usage-unknown-malformed"
    write_digest_log(
        base,
        [
            "2026-07-06 14:45:54 INFO ask_grok_for_reply - xAI usage={'prompt_tokens': 539, 'completion_tokens': 18, 'total_tokens': 1109, 'prompt_tokens_details': {'cached_tokens': 128}, 'completion_tokens_details': {'reasoning_tokens': 552}, 'num_sources_used': 0, 'cost_in_usd_ticks': 19643500}",
            "2026-07-06 14:46:00 INFO ask_grok_for_reply - xAI usage={'prompt_tokens': bad",
        ],
    )

    digest = run_digest(base)
    assert digest.returncode == 0, digest.stderr
    assert "## xAI usage" in digest.stdout
    assert "| 2026-07-06 14:45:54 | unknown |  | 539 | 128 | 552 | 18 | 1109 | 0 | 19643500 |" in digest.stdout
    assert "successful_xai_calls = 1" in digest.stdout
    assert "Malformed xAI usage records" in digest.stdout
    assert "could not parse xAI usage dictionary" in digest.stdout


def test_digest_associates_xai_usage_with_quote_tweet_context(tmp_path: Path) -> None:
    base = tmp_path / "digest-xai-usage-quote-tweet"
    write_digest_log(
        base,
        [
            "2026-07-06 12:00:00 INFO maybe_reply_to_quote_tweets:4000 - Considering quote tweet id=999 author_id=777 original_post_id=555 text='Interesting'",
            "2026-07-06 12:00:01 INFO ask_grok_for_reply:5315 - Asking Grok for reply. context_text=\"A user has quote-posted one of this account's posts.\"",
            "2026-07-06 12:00:02 INFO ask_grok_for_reply:5396 - xAI usage={'prompt_tokens': 100, 'completion_tokens': 5, 'total_tokens': 150, 'prompt_tokens_details': {'cached_tokens': 25}, 'completion_tokens_details': {'reasoning_tokens': 45}, 'num_sources_used': 1, 'cost_in_usd_ticks': 123}",
            "2026-07-06 12:00:03 INFO ask_grok_for_reply:5414 - Grok generated usable reply: 'A reply.'",
        ],
    )

    digest = run_digest(base)
    assert digest.returncode == 0, digest.stderr
    assert "| 2026-07-06 12:00:02 | quote-tweet | 999 | 100 | 25 | 45 | 5 | 150 | 1 | 123 |" in digest.stdout


def test_digest_does_not_reuse_completed_mention_xai_context(tmp_path: Path) -> None:
    base = tmp_path / "digest-xai-usage-stale-mention"
    write_digest_log(
        base,
        [
            "2026-07-06 15:00:00 INFO maybe_reply_to_mentions:3000 - Considering mention id=123 author_id=456 text='@MrsMThatcher hello'",
            "2026-07-06 15:00:01 INFO ask_grok_for_reply:5315 - Asking Grok for reply. context_text='Incoming post/comment to answer:\\n@MrsMThatcher hello'",
            "2026-07-06 15:00:02 INFO ask_grok_for_reply:5396 - xAI usage={'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 20, 'prompt_tokens_details': {'cached_tokens': 1}, 'completion_tokens_details': {'reasoning_tokens': 8}, 'num_sources_used': 0, 'cost_in_usd_ticks': 100}",
            "2026-07-06 15:00:03 INFO maybe_reply_to_mentions:3050 - Generated reply to mention 123: 'A reply.'",
            "2026-07-06 15:00:04 INFO ask_grok_for_reply:5396 - xAI usage={'prompt_tokens': 11, 'completion_tokens': 3, 'total_tokens': 21, 'prompt_tokens_details': {'cached_tokens': 2}, 'completion_tokens_details': {'reasoning_tokens': 7}, 'num_sources_used': 0, 'cost_in_usd_ticks': 101}",
        ],
    )

    digest = run_digest(base)
    assert digest.returncode == 0, digest.stderr
    assert "| 2026-07-06 15:00:02 | mention | 123 | 10 | 1 | 8 | 2 | 20 | 0 | 100 |" in digest.stdout
    assert "| 2026-07-06 15:00:04 | unknown |  | 11 | 2 | 7 | 3 | 21 | 0 | 101 |" in digest.stdout


def test_digest_does_not_reuse_completed_quote_tweet_xai_context(tmp_path: Path) -> None:
    base = tmp_path / "digest-xai-usage-stale-quote-tweet"
    write_digest_log(
        base,
        [
            "2026-07-06 12:00:00 INFO maybe_reply_to_quote_tweets:4000 - Considering quote tweet id=999 author_id=777 original_post_id=555 text='Interesting'",
            "2026-07-06 12:00:01 INFO ask_grok_for_reply:5315 - Asking Grok for reply. context_text=\"A user has quote-posted one of this account's posts.\"",
            "2026-07-06 12:00:02 INFO ask_grok_for_reply:5396 - xAI usage={'prompt_tokens': 100, 'completion_tokens': 5, 'total_tokens': 150, 'prompt_tokens_details': {'cached_tokens': 25}, 'completion_tokens_details': {'reasoning_tokens': 45}, 'num_sources_used': 1, 'cost_in_usd_ticks': 123}",
            "2026-07-06 12:00:03 INFO maybe_reply_to_quote_tweets:4020 - Generated reply to quote tweet 999: 'A reply.'",
            "2026-07-06 12:00:04 INFO ask_grok_for_reply:5396 - xAI usage={'prompt_tokens': 101, 'completion_tokens': 6, 'total_tokens': 151, 'prompt_tokens_details': {'cached_tokens': 26}, 'completion_tokens_details': {'reasoning_tokens': 46}, 'num_sources_used': 0, 'cost_in_usd_ticks': 124}",
        ],
    )

    digest = run_digest(base)
    assert digest.returncode == 0, digest.stderr
    assert "| 2026-07-06 12:00:02 | quote-tweet | 999 | 100 | 25 | 45 | 5 | 150 | 1 | 123 |" in digest.stdout
    assert "| 2026-07-06 12:00:04 | unknown |  | 101 | 26 | 46 | 6 | 151 | 0 | 124 |" in digest.stdout


def test_digest_does_not_attribute_mention_xai_usage_to_stale_locally_skipped_quote_tweet(tmp_path: Path) -> None:
    base = tmp_path / "digest-xai-usage-stale-quote-to-mention"
    quote_a = "2074443670913184251"
    quote_b = "2074425517856428052"
    mention_c = "2074472113872769394"
    write_digest_log(
        base,
        [
            f"2026-07-07 12:51:00 INFO maybe_reply_to_quote_tweets:4000 - Considering quote tweet id={quote_a} author_id=777 original_post_id=555 text='Now the Brits arrest you for tweets.'",
            "2026-07-07 12:51:01 INFO ask_grok_for_reply:5315 - Asking Grok for reply. context_text=\"A user has quote-posted one of this account's posts.\"",
            "2026-07-07 12:51:04 INFO ask_grok_for_reply:5396 - xAI usage={'prompt_tokens': 100, 'completion_tokens': 5, 'total_tokens': 150, 'prompt_tokens_details': {'cached_tokens': 25}, 'completion_tokens_details': {'reasoning_tokens': 45}, 'num_sources_used': 1, 'cost_in_usd_ticks': 123}",
            f"2026-07-07 12:51:04 INFO maybe_reply_to_quote_tweets:4020 - No usable reply generated for quote tweet {quote_a}",
            f"2026-07-07 12:51:05 INFO maybe_reply_to_quote_tweets:4000 - Considering quote tweet id={quote_b} author_id=888 original_post_id=555 text='Proprio quello che mancava nel 1978.'",
            f"2026-07-07 12:51:05 INFO maybe_reply_to_quote_tweets:4010 - Skipping quote tweet {quote_b}: already seen/replied/skipped",
            f"2026-07-07 13:35:11 INFO maybe_reply_to_mentions:3000 - Considering mention id={mention_c} author_id=999 text='@MrsMThatcher Me too, GOAT ♥️♥️'",
            "2026-07-07 13:35:12 INFO ask_grok_for_reply:5315 - Asking Grok for reply. context_text='Incoming post/comment to answer:\\n@MrsMThatcher Me too, GOAT ♥️♥️'",
            "2026-07-07 13:35:20 INFO ask_grok_for_reply:5396 - xAI usage={'prompt_tokens': 458, 'completion_tokens': 11, 'total_tokens': 1147, 'prompt_tokens_details': {'cached_tokens': 128}, 'completion_tokens_details': {'reasoning_tokens': 678}, 'num_sources_used': 0, 'cost_in_usd_ticks': 2160600}",
            f"2026-07-07 13:35:21 INFO maybe_reply_to_mentions:3050 - Generated reply to mention {mention_c}: 'Many do. It saves a great deal of time.'",
            "2026-07-07 13:35:22 INFO maybe_reply_to_mentions:3100 - Reply posted successfully",
        ],
    )

    digest = run_digest(base)
    assert digest.returncode == 0, digest.stderr
    assert f"| 2026-07-07 12:51:04 | quote-tweet | {quote_a} | 100 | 25 | 45 | 5 | 150 | 1 | 123 |" in digest.stdout
    assert f"| 2026-07-07 13:35:20 | mention | {mention_c} | 458 | 128 | 678 | 11 | 1147 | 0 | 2160600 |" in digest.stdout
    assert f"| 2026-07-07 13:35:20 | quote-tweet | {quote_b} |" not in digest.stdout


def test_digest_local_quote_tweet_skip_does_not_create_xai_context(tmp_path: Path) -> None:
    base = tmp_path / "digest-xai-usage-local-quote-skip"
    quote_b = "2074425517856428052"
    write_digest_log(
        base,
        [
            f"2026-07-07 12:51:05 INFO maybe_reply_to_quote_tweets:4000 - Considering quote tweet id={quote_b} author_id=888 original_post_id=555 text='Proprio quello che mancava nel 1978.'",
            f"2026-07-07 12:51:05 INFO maybe_reply_to_quote_tweets:4010 - Skipping quote tweet {quote_b}: already seen/replied/skipped",
            "2026-07-07 13:00:00 INFO ask_grok_for_reply:5396 - xAI usage={'prompt_tokens': 12, 'completion_tokens': 3, 'total_tokens': 40, 'prompt_tokens_details': {'cached_tokens': 2}, 'completion_tokens_details': {'reasoning_tokens': 25}, 'num_sources_used': 0, 'cost_in_usd_ticks': 44}",
        ],
    )

    digest = run_digest(base)
    assert digest.returncode == 0, digest.stderr
    assert "| 2026-07-07 13:00:00 | unknown |  | 12 | 2 | 25 | 3 | 40 | 0 | 44 |" in digest.stdout
    assert f"| 2026-07-07 13:00:00 | quote-tweet | {quote_b} |" not in digest.stdout


def test_digest_local_mention_skip_does_not_poison_later_quote_tweet_xai_context(tmp_path: Path) -> None:
    base = tmp_path / "digest-xai-usage-local-mention-skip"
    mention_a = "111"
    quote_b = "222"
    write_digest_log(
        base,
        [
            f"2026-07-07 10:00:00 INFO maybe_reply_to_mentions:3000 - Considering mention id={mention_a} author_id=456 text='@MrsMThatcher spam'",
            f"2026-07-07 10:00:01 INFO maybe_reply_to_mentions:3078 - Skipping mention {mention_a}: spam/not worth replying",
            f"2026-07-07 10:01:00 INFO maybe_reply_to_quote_tweets:4000 - Considering quote tweet id={quote_b} author_id=777 original_post_id=555 text='Interesting'",
            "2026-07-07 10:01:01 INFO ask_grok_for_reply:5315 - Asking Grok for reply. context_text=\"A user has quote-posted one of this account's posts.\"",
            "2026-07-07 10:01:02 INFO ask_grok_for_reply:5396 - xAI usage={'prompt_tokens': 20, 'completion_tokens': 4, 'total_tokens': 60, 'prompt_tokens_details': {'cached_tokens': 3}, 'completion_tokens_details': {'reasoning_tokens': 36}, 'num_sources_used': 1, 'cost_in_usd_ticks': 55}",
            f"2026-07-07 10:01:03 INFO maybe_reply_to_quote_tweets:4020 - Generated reply to quote tweet {quote_b}: 'A reply.'",
        ],
    )

    digest = run_digest(base)
    assert digest.returncode == 0, digest.stderr
    assert f"| 2026-07-07 10:01:02 | quote-tweet | {quote_b} | 20 | 3 | 36 | 4 | 60 | 1 | 55 |" in digest.stdout
    assert f"| 2026-07-07 10:01:02 | mention | {mention_a} |" not in digest.stdout


def test_digest_keeps_consecutive_mixed_lane_xai_contexts_in_order(tmp_path: Path) -> None:
    base = tmp_path / "digest-xai-usage-consecutive-mixed"
    mention_a = "301"
    quote_b = "302"
    mention_c = "303"
    write_digest_log(
        base,
        [
            f"2026-07-07 11:00:00 INFO maybe_reply_to_mentions:3000 - Considering mention id={mention_a} author_id=401 text='@MrsMThatcher one'",
            "2026-07-07 11:00:01 INFO ask_grok_for_reply:5315 - Asking Grok for reply. context_text='Incoming post/comment to answer:\\n@MrsMThatcher one'",
            "2026-07-07 11:00:02 INFO ask_grok_for_reply:5396 - xAI usage={'prompt_tokens': 30, 'completion_tokens': 3, 'total_tokens': 70, 'prompt_tokens_details': {'cached_tokens': 4}, 'completion_tokens_details': {'reasoning_tokens': 37}, 'num_sources_used': 0, 'cost_in_usd_ticks': 101}",
            f"2026-07-07 11:00:03 INFO maybe_reply_to_mentions:3050 - Generated reply to mention {mention_a}: 'Reply one.'",
            f"2026-07-07 11:01:00 INFO maybe_reply_to_quote_tweets:4000 - Considering quote tweet id={quote_b} author_id=402 original_post_id=555 text='Two'",
            "2026-07-07 11:01:01 INFO ask_grok_for_reply:5315 - Asking Grok for reply. context_text=\"A user has quote-posted one of this account's posts.\"",
            "2026-07-07 11:01:02 INFO ask_grok_for_reply:5396 - xAI usage={'prompt_tokens': 31, 'completion_tokens': 4, 'total_tokens': 71, 'prompt_tokens_details': {'cached_tokens': 5}, 'completion_tokens_details': {'reasoning_tokens': 36}, 'num_sources_used': 1, 'cost_in_usd_ticks': 102}",
            f"2026-07-07 11:01:03 INFO maybe_reply_to_quote_tweets:4020 - No usable reply generated for quote tweet {quote_b}",
            f"2026-07-07 11:02:00 INFO maybe_reply_to_mentions:3000 - Considering mention id={mention_c} author_id=403 text='@MrsMThatcher three'",
            "2026-07-07 11:02:01 INFO ask_grok_for_reply:5315 - Asking Grok for reply. context_text='Incoming post/comment to answer:\\n@MrsMThatcher three'",
            "2026-07-07 11:02:02 INFO ask_grok_for_reply:5396 - xAI usage={'prompt_tokens': 32, 'completion_tokens': 5, 'total_tokens': 72, 'prompt_tokens_details': {'cached_tokens': 6}, 'completion_tokens_details': {'reasoning_tokens': 35}, 'num_sources_used': 0, 'cost_in_usd_ticks': 103}",
            f"2026-07-07 11:02:03 INFO maybe_reply_to_mentions:3050 - Generated reply to mention {mention_c}: 'Reply three.'",
        ],
    )

    digest = run_digest(base)
    assert digest.returncode == 0, digest.stderr
    assert f"| 2026-07-07 11:00:02 | mention | {mention_a} | 30 | 4 | 37 | 3 | 70 | 0 | 101 |" in digest.stdout
    assert f"| 2026-07-07 11:01:02 | quote-tweet | {quote_b} | 31 | 5 | 36 | 4 | 71 | 1 | 102 |" in digest.stdout
    assert f"| 2026-07-07 11:02:02 | mention | {mention_c} | 32 | 6 | 35 | 5 | 72 | 0 | 103 |" in digest.stdout


def test_digest_carries_active_xai_context_across_resume_boundary(tmp_path: Path) -> None:
    base = tmp_path / "digest-xai-resume-boundary"
    state_file = tmp_path / "digest-state.json"
    mention_id = "2074728963755196898"
    reply_id = "2074730135442374791"
    first_window = [
        f"2026-07-08 06:39:38 INFO maybe_reply_to_mentions:6132 - Considering mention id={mention_id} author_id=1394717514475704320 text='@MrsMThatcher socialism works?'",
        "2026-07-08 06:39:38 INFO ask_grok_for_reply:5682 - Asking Grok for reply. context_text='Incoming post/comment to answer:\\n@MrsMThatcher socialism works?'",
    ]
    second_window = [
        "2026-07-08 06:39:45 INFO ask_grok_for_reply:5784 - xAI usage={'prompt_tokens': 529, 'completion_tokens': 29, 'total_tokens': 1194, 'prompt_tokens_details': {'cached_tokens': 128}, 'completion_tokens_details': {'reasoning_tokens': 636}, 'num_sources_used': 0, 'cost_in_usd_ticks': 21893500}",
        f"2026-07-08 06:39:45 INFO maybe_reply_to_mentions:6224 - Generated reply to mention {mention_id}: 'Poverty is the absence of wealth.'",
        f"2026-07-08 06:39:45 INFO create_post:3236 - Created X post successfully. response={{'data': {{'text': 'Poverty is the absence of wealth.', 'id': '{reply_id}'}}}}",
        "2026-07-08 06:39:45 INFO maybe_reply_to_mentions:6357 - Reply posted successfully",
    ]

    write_digest_log(base, first_window)
    first_digest = run_digest(base, state_file=state_file)
    assert first_digest.returncode == 0, first_digest.stderr

    write_digest_log(base, [*first_window, *second_window])
    second_digest = run_digest(base, state_file=state_file)
    assert second_digest.returncode == 0, second_digest.stderr
    assert f"| 2026-07-08 06:39:45 | mention | {mention_id} | 529 | 128 | 636 | 29 | 1194 | 0 | 21893500 |" in second_digest.stdout
    assert f"| 2026-07-08 06:39:45 | {mention_id} | 1394717514475704320 | @MrsMThatcher socialism works? | Poverty is the absence of wealth. | {reply_id} |" in second_digest.stdout
    assert "| 2026-07-08 06:39:45 | unknown |" not in second_digest.stdout


def test_digest_does_not_carry_completed_xai_context_across_resume_boundary(tmp_path: Path) -> None:
    base = tmp_path / "digest-xai-completed-resume-boundary"
    state_file = tmp_path / "digest-state.json"
    write_digest_log(
        base,
        [
            "2026-07-08 06:39:38 INFO maybe_reply_to_mentions:6132 - Considering mention id=123 author_id=456 text='@MrsMThatcher hello'",
            "2026-07-08 06:39:38 INFO ask_grok_for_reply:5682 - Asking Grok for reply. context_text='Incoming post/comment to answer:\\n@MrsMThatcher hello'",
            "2026-07-08 06:39:38 INFO ask_grok_for_reply:5784 - xAI usage={'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 20, 'prompt_tokens_details': {'cached_tokens': 1}, 'completion_tokens_details': {'reasoning_tokens': 8}, 'num_sources_used': 0, 'cost_in_usd_ticks': 100}",
            "2026-07-08 06:39:38 INFO maybe_reply_to_mentions:6224 - Generated reply to mention 123: 'A reply.'",
        ],
    )
    first_digest = run_digest(base, state_file=state_file)
    assert first_digest.returncode == 0, first_digest.stderr

    write_digest_log(
        base,
        [
            "2026-07-08 06:39:38 INFO maybe_reply_to_mentions:6132 - Considering mention id=123 author_id=456 text='@MrsMThatcher hello'",
            "2026-07-08 06:39:38 INFO ask_grok_for_reply:5682 - Asking Grok for reply. context_text='Incoming post/comment to answer:\\n@MrsMThatcher hello'",
            "2026-07-08 06:39:38 INFO ask_grok_for_reply:5784 - xAI usage={'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 20, 'prompt_tokens_details': {'cached_tokens': 1}, 'completion_tokens_details': {'reasoning_tokens': 8}, 'num_sources_used': 0, 'cost_in_usd_ticks': 100}",
            "2026-07-08 06:39:38 INFO maybe_reply_to_mentions:6224 - Generated reply to mention 123: 'A reply.'",
            "2026-07-08 06:40:00 INFO ask_grok_for_reply:5784 - xAI usage={'prompt_tokens': 11, 'completion_tokens': 3, 'total_tokens': 21, 'prompt_tokens_details': {'cached_tokens': 2}, 'completion_tokens_details': {'reasoning_tokens': 7}, 'num_sources_used': 0, 'cost_in_usd_ticks': 101}",
        ],
    )
    second_digest = run_digest(base, state_file=state_file)
    assert second_digest.returncode == 0, second_digest.stderr
    assert "| 2026-07-08 06:40:00 | unknown |  | 11 | 2 | 7 | 3 | 21 | 0 | 101 |" in second_digest.stdout
    assert "| 2026-07-08 06:40:00 | mention | 123 |" not in second_digest.stdout


def test_digest_new_request_beats_carried_pending_xai_candidate(tmp_path: Path) -> None:
    base = tmp_path / "digest-xai-carried-pending-tie"
    state_file = tmp_path / "digest-state.json"
    old_quote = "900"
    new_mention = "901"
    write_digest_log(
        base,
        [
            f"2026-07-08 06:39:38 INFO maybe_reply_to_quote_tweets:4000 - Considering quote tweet id={old_quote} author_id=777 original_post_id=555 text='Old quote'",
            "2026-07-08 06:39:38 INFO ask_grok_for_reply:5682 - Asking Grok for reply. context_text=\"A user has quote-posted one of this account's posts.\"",
        ],
    )
    first_digest = run_digest(base, state_file=state_file)
    assert first_digest.returncode == 0, first_digest.stderr

    write_digest_log(
        base,
        [
            f"2026-07-08 06:39:38 INFO maybe_reply_to_quote_tweets:4000 - Considering quote tweet id={old_quote} author_id=777 original_post_id=555 text='Old quote'",
            "2026-07-08 06:39:38 INFO ask_grok_for_reply:5682 - Asking Grok for reply. context_text=\"A user has quote-posted one of this account's posts.\"",
            f"2026-07-08 06:40:00 INFO maybe_reply_to_mentions:6132 - Considering mention id={new_mention} author_id=456 text='@MrsMThatcher new mention'",
            "2026-07-08 06:40:00 INFO ask_grok_for_reply:5682 - Asking Grok for reply. context_text='Incoming post/comment to answer:\\n@MrsMThatcher new mention'",
            "2026-07-08 06:40:01 INFO ask_grok_for_reply:5784 - xAI usage={'prompt_tokens': 11, 'completion_tokens': 3, 'total_tokens': 21, 'prompt_tokens_details': {'cached_tokens': 2}, 'completion_tokens_details': {'reasoning_tokens': 7}, 'num_sources_used': 0, 'cost_in_usd_ticks': 101}",
        ],
    )
    second_digest = run_digest(base, state_file=state_file)
    assert second_digest.returncode == 0, second_digest.stderr
    assert f"| 2026-07-08 06:40:01 | mention | {new_mention} | 11 | 2 | 7 | 3 | 21 | 0 | 101 |" in second_digest.stdout
    assert f"| 2026-07-08 06:40:01 | quote-tweet | {old_quote} |" not in second_digest.stdout

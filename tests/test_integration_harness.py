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

from tests.fake_api_server import FakeApiServer, load_scenario
from tests.helpers.integration_harness import (
    SCENARIOS,
    collapse_quote_whitespace,
    read_json,
    write_json,
    write_private_json,
    base_test_env,
    prepare_base_dir,
    persist_test_runtime_state,
    run_bot_command,
    run_cycle,
)


pytestmark = pytest.mark.allow_loopback_network

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / "mrsMThatcher2.py"
DIGEST = ROOT / "mrs_log_digest.py"
PRODUCTION_BASE_DIR = Path("/disks/disk1/etc/mrsMThatcher")


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
    env.pop("OPENAI_API_BASE_URL", None)
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
            "OPENAI_API_KEY": "dummy",
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


def run_digest(
    base_dir: Path,
    *,
    state_file: Path | None = None,
    since: str | None = None,
    until: str | None = None,
    as_json: bool = False,
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
    if since is not None:
        args.extend(["--since", since])
    if until is not None:
        args.extend(["--until", until])
    if as_json:
        args.append("--json")
    args.append(str(base_dir / "test.log"))
    return subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def run_digest_inputs(
    base_dir: Path,
    inputs: list[Path],
    *,
    state_file: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run JSON digest mode against an explicit ordered set of test logs."""

    args = [
        sys.executable,
        str(DIGEST),
        "--project-dir",
        str(base_dir),
        "--glob",
        "__no_implicit_test_logs__",
    ]
    if state_file is None:
        args.append("--no-state")
    else:
        args.extend(["--state-file", str(state_file)])
    args.append("--json")
    args.extend(str(path) for path in inputs)
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


def digest_event_line(timestamp: str, event: str, **fields: object) -> str:
    payload = {"event": event, **fields}
    return (
        f"{timestamp} INFO     log_event:330 - EVENT "
        + json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def pipeline_digest_lines(
    *,
    target_id: str,
    status: str,
    reason: str,
    model_call_count: int,
    revision_count: int,
    route_source: str,
    xai_gate_decision: str,
    provider_call_counts: dict[str, int],
    mode: str | None = None,
) -> list[str]:
    """Build the paired decision/stage records for one tested-pipeline result."""
    decision_fields: dict[str, object] = {
        "status": status,
        "lane": "mention",
        "target_id": target_id,
        "strategy_version": "tested-reply-pipeline-20260817",
        "reason": reason,
        "reply_requirement": "general" if xai_gate_decision == "reply" else None,
        "route_source": route_source,
        "model_call_count": model_call_count,
        "revision_count": revision_count,
        "author_quarantine_evidence": None,
        "pipeline_stage_status": status,
        "effective_status": status,
        "effective_reason": reason,
    }
    if mode is not None:
        decision_fields["mode"] = mode
    return [
        digest_event_line(
            "2026-08-31 09:14:23",
            "ai_reply_pipeline_decision",
            **decision_fields,
        ),
        digest_event_line(
            "2026-08-31 09:14:24",
            "ai_reply_pipeline_stage_summary",
            status=status,
            lane="mention",
            target_id=target_id,
            strategy_version="tested-reply-pipeline-20260817",
            terminal_reason=reason,
            effective_status=status,
            effective_reason=reason,
            reply_requirement=(
                "general" if xai_gate_decision == "reply" else None
            ),
            route_source=route_source,
            xai_gate_decision=xai_gate_decision,
            model_call_count=model_call_count,
            revision_count=revision_count,
            provider_call_counts=provider_call_counts,
        ),
    ]


def digest_markdown_section(markdown: str, title: str) -> str:
    marker = f"## {title}\n"
    assert marker in markdown
    return markdown.split(marker, 1)[1].split("\n## ", 1)[0]


def write_treatment_quote_digest_fixture(base_dir: Path) -> dict[str, object]:
    post_id = "2094174293231812903"
    quote_hash = "67d4ca636be56c737a34bc9332278a8ba4293a2625825404942ef0123a8bdc56"
    pair_id = "pair-3ae2bf3b69a07dd3a7b605cf"
    plan_sha256 = "d543b32b36d6fc8f9a27b3765d9bfecfcc166f22ca1d152b5ff0ac8c3c988d06"
    quote_text = (
        "Marxism-Leninism is simply not capable of producing either political "
        "freedom or economic success, because it does not recognise the dignity "
        "of the individual or his desire to better himself."
    )
    question = (
        "Can state control bring prosperity without denying individual dignity?"
    )
    public_text = quote_text + "\n\nQuestion — " + question
    x_response_text = public_text + " https://t.co/example-media"
    write_digest_log(
        base_dir,
        [
            "2026-08-30 22:23:50 INFO     select_quote_candidate:4630 - "
            f"Selected quote line_no=398 quote_hash={quote_hash} "
            "weight=1.00 seasonal_boost=False",
            "2026-08-30 22:23:51 INFO     choose_matched_unused_image:4931 - "
            "Selected matched image basename=t12.jpg image_no=11 score=25.4 "
            "components=historical=8.0",
            "2026-08-30 22:23:53 INFO     create_post:3204 - "
            "Created X post successfully response="
            f"{{'data': {{'id': '{post_id}', 'text': {x_response_text!r}}}}}",
            digest_event_line(
                "2026-08-30 22:23:54",
                "engagement_question_experimental_member_confirmed",
                arm="treatment",
                member_position=2,
                pair_id=pair_id,
                plan_sha256=plan_sha256,
                post_id=post_id,
                publication_sequence=2,
            ),
            digest_event_line(
                "2026-08-30 22:23:54",
                "main_post_posted",
                lane="quote_image",
                post_id=post_id,
                line_no=398,
                image_no=11,
                image_basename="t12.jpg",
                image_score=25.4,
                quote_hash=quote_hash,
                engagement_experiment_id="substantive-question-v1",
                engagement_experiment_arm="treatment",
                engagement_experiment_member_position=2,
                engagement_experiment_pair_id=pair_id,
                engagement_experiment_plan_sha256=plan_sha256,
                engagement_experiment_publication_order="control_first",
                engagement_experiment_sequence=2,
                engagement_question_present=True,
                engagement_approved_question_sha256=hashlib.sha256(
                    question.encode("utf-8")
                ).hexdigest(),
                engagement_public_text_sha256=hashlib.sha256(
                    public_text.encode("utf-8")
                ).hexdigest(),
            ),
            digest_event_line(
                "2026-08-30 22:23:55",
                "account_root_posted",
                event_version=1,
                lane="quote_image",
                post_id=post_id,
                root_post_id=post_id,
                conversation_id=post_id,
                quote_id=quote_hash,
                quote_text=quote_text,
                public_text=public_text,
                visible_text_source="public_text",
                publication_authority="confirmed_transport",
            ),
            "2026-08-30 22:23:55 INFO     post_random_quote:5198 - "
            f"Quote/image posted successfully. posted_id={post_id}",
        ],
    )
    return {
        "post_id": post_id,
        "quote_hash": quote_hash,
        "pair_id": pair_id,
        "plan_sha256": plan_sha256,
        "quote_text": quote_text,
        "question": question,
        "public_text": public_text,
    }


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
    assert len(fake_server.openai_requests) == 1
    assert fake_server.xai_requests == []
    payload = json.loads(fake_server.openai_requests[0]["input"])
    assert payload["lane"] == "mention"
    assert payload["identities"]["target_post_id"] == "100"
    assert payload["visible_conversation"][-1]["post_id"] == "100"
    assert sum(
        turn["post_id"] == "100" for turn in payload["visible_conversation"]
    ) == 1
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
    corpus_dir = (
        base_dir / "semantic_alignment_research" / "quote_research_full_001"
    )
    corpus_dir.rename(base_dir / "missing-research-corpus")

    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.openai_requests == []
    assert fake_server.xai_requests == []
    assert fake_server.uploads == []
    assert fake_server.posts == []
    assert "reply_evidence_unavailable" in result.stdout
    state = read_json(base_dir / "bot_state.json")
    assert state.get("openai_error_epochs", []) == []


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
        "Responsibility matters more than rhetoric.",
        "I favour individual choice.",
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
                "QUOTE_CHECK_SPACING_RETRY_SECONDS": 1,
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


def test_production_tick_advances_isolated_genuine_progress_health(
    tmp_path: Path,
) -> None:
    server = FakeApiServer({}).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "last_reply_epoch": 0,
                "last_reply_check_epoch": 2_000_000_000,
                "last_quote_tweet_check_epoch": 2_000_000_000,
            },
            local_config={
                "ENABLE_AUTO_REPLIES": False,
                "ENABLE_QUOTE_TWEET_CHECKS": False,
                "ENABLE_DAILY_MEME_POSTS": False,
            },
        )
        health_path = base_dir / "test-bot-health.json"
        result = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={
                "MRS_FAKE_NOW_EPOCH": "2000000000",
                "MRS_BOT_HEALTH_FILE": str(health_path),
            },
        )

        assert result.returncode == 0, result.stderr + result.stdout
        snapshot = read_json(health_path)
        assert snapshot["schema_version"] == 1
        assert snapshot["progress_sequence"] >= 6
        assert snapshot["phase"] == "shutdown"
        assert snapshot["last_loop_started_epoch"] is not None
        assert snapshot["last_loop_completed_epoch"] is not None
        assert server.requests == []
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
def test_global_900_reply_spacing_blocks_cross_lane_until_boundary(
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
        "I favour individual choice.",
        "Responsibility matters more than rhetoric.",
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
                "MIN_SECONDS_BETWEEN_REPLIES": 900,
                "REPLY_CHECK_EVERY_SECONDS": 1,
                "QUOTE_CHECK_EVERY_SECONDS": 1,
                "QUOTE_CHECK_SPACING_RETRY_SECONDS": 1,
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
            extra_env={"MRS_FAKE_NOW_EPOCH": str(start + 899)},
        )
        assert blocked.returncode == 0, blocked.stderr + blocked.stdout
        assert len(server.requests) == requests_after_first
        assert [post["reply"]["in_reply_to_tweet_id"] for post in server.posts] == expected_targets[:1]
        assert read_json(base_dir / "bot_state.json")["last_reply_epoch"] == start

        boundary = run_bot_command(
            base_dir,
            server,
            "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": str(start + 900)},
        )
        assert boundary.returncode == 0, boundary.stderr + boundary.stdout
        assert [post["reply"]["in_reply_to_tweet_id"] for post in server.posts] == expected_targets
        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_epoch"] == start + 900
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
        assert len(quote_search_requests(server)) == 1
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
        assert server.openai_requests == []
        assert server.xai_requests == []
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
        assert server.openai_requests == []
        assert server.xai_requests == []
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
            "grok_replies": ["Clarity matters."],
            "error_paths": {"/2/tweets/search/recent": 503},
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
                "QUOTE_CHECK_SPACING_RETRY_SECONDS": 1,
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

        assert len(quote_search_requests(server)) == 3
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
        "I favour individual choice.",
        "Responsibility matters more than rhetoric.",
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
        "Responsibility matters more than rhetoric.",
        "I favour individual choice.",
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
        assert len(quote_search_requests(server)) == 1
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
        "I favour individual choice.",
        "Responsibility matters more than rhetoric.",
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
        assert len(quote_search_requests(server)) <= 8
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
            "I favour individual choice.",
            "Responsibility matters more than rhetoric.",
            "Liberty must come first.",
            "I value evidence.",
            "I favour accountability.",
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
                persist_test_runtime_state(
                    base_dir,
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
                persist_test_runtime_state(base_dir, state)

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
        assert len(quote_search_requests(server)) < len(tick_epochs)
    finally:
        server.stop()


def test_clock_rollback_repairs_quote_epoch_once_then_polls_after_interval(tmp_path: Path) -> None:
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
        assert len(quote_search_requests(server)) == 0
        state = read_json(base_dir / "bot_state.json")
        assert state["last_reply_check_epoch"] == 2_000_000_000
        assert state["last_quote_tweet_check_epoch"] == 2_000_000_000

        for epoch in (2_000_000_001, 2_000_000_899):
            result = run_bot_command(
                base_dir, server, "--test-main-tick",
                extra_env={"MRS_FAKE_NOW_EPOCH": str(epoch)},
            )
            assert result.returncode == 0, result.stderr + result.stdout
            assert len(quote_search_requests(server)) == 0
            assert read_json(base_dir / "bot_state.json")["last_quote_tweet_check_epoch"] == 2_000_000_000

        result = run_bot_command(
            base_dir, server, "--test-main-tick",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000900"},
        )
        assert result.returncode == 0, result.stderr + result.stdout
        assert len(quote_search_requests(server)) == 1
        assert read_json(base_dir / "bot_state.json")["last_quote_tweet_check_epoch"] == 2_000_000_900
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
            "grok_replies": ["Responsibility matters more than rhetoric."],
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
        assert len(quote_search_requests(server)) == 1
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
        assert server.openai_requests == []
        assert server.xai_requests == []
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
                "OPENAI_API_BASE_URL": "http://127.0.0.1:1/v1",
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


def test_transient_parent_fetch_failure_keeps_candidate_after_traversal_completes(tmp_path: Path) -> None:
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
        assert server.path_counts.get("/v1/responses", 0) == 0
        assert server.xai_requests == []
        state = read_json(base_dir / "bot_state.json")
        assert state["last_seen_mention_id"] == "100"
        assert set(state["mention_pending_candidates"]) == {"100"}
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
            "grok_replies": ["I favour individual choice."],
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
        assert state["last_seen_mention_id"] == "101"
        assert set(state["mention_pending_candidates"]) == {"101"}
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
        "tweets": {
            "700": {
                "id": "700",
                "text": "A watched account post.",
                "author_id": "12345",
                "conversation_id": "700",
            }
        },
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
        "grok_replies": ["I favour individual choice."],
    }
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(tmp_path, watch_ids=["700"])

        first = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"})
        assert first.returncode == 0, first.stderr + first.stdout
        assert len(server.openai_requests) == 1
        assert server.xai_requests == []
        payload = json.loads(server.openai_requests[0]["input"])
        assert payload["lane"] == "hot_post_reply"
        assert payload["identities"]["root_post_id"] == "700"
        assert payload["visible_conversation"][-1]["post_id"] == "300"
        assert sum(
            turn["post_id"] == "300"
            for turn in payload["visible_conversation"]
        ) == 1
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
        scenario["grok_replies"] = ["Responsibility matters."]
        third = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000004"})
        assert third.returncode == 0, third.stderr + third.stdout
        assert any(req["path"] == "/2/tweets/search/recent" and req["query"].get("since_id") == ["301"] for req in server.requests)
        state = read_json(base_dir / "bot_state.json")
        assert "302" in state["replied_to_ids"]
        assert state["hot_post_reply_since_ids"]["700"] == "301"

        scenario["search_recent"] = [
            {
                "id": "303",
                "text": "The model will decline this usable candidate",
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
        assert state["skipped_hot_reply_records"]["303"]["reason"] == (
            "editorial_no_reply:no_meaningful_content"
        )
        assert state["skipped_hot_reply_records"]["303"]["retryable"] is False

        state["hot_post_reply_check_counts"]["700"] = 11
        state["last_reply_epoch"] = 0
        persist_test_runtime_state(base_dir, state)
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
        before_openai = len(server.openai_requests)
        fifth = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000008"})
        assert fifth.returncode == 0, fifth.stderr + fifth.stdout
        recent_searches = [req for req in server.requests if req["path"] == "/2/tweets/search/recent"]
        assert "since_id" not in recent_searches[-1]["query"]
        assert len(server.posts) == before_posts
        assert len(server.openai_requests) == before_openai
        assert server.xai_requests == []
    finally:
        server.stop()


def test_duplicate_mention_hot_post_counts_and_state_increment_once(tmp_path: Path) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "duplicate_mention_hot_post.json")).start()
    try:
        base_dir = prepare_base_dir(tmp_path, watch_ids=["700"])
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        assert len(server.openai_requests) == 1
        assert server.xai_requests == []
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

        # Advance through the actual spacing boundary; do not rewrite a sealed
        # generation behind the state writer between subprocess restarts.
        posted = run_bot_command(base_dir, server, extra_env={"MRS_FAKE_NOW_EPOCH": str(fake_now + 3600)})
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
        expired_base = prepare_base_dir(tmp_path / "expired", control={"pause_replies_until": "2000-01-01T00:00:00Z"})
        result = run_cycle(expired_base, expired_server)
        assert result.returncode == 0, result.stderr + result.stdout
        assert len(expired_server.posts) == 1
    finally:
        expired_server.stop()


@pytest.mark.parametrize("local_config,expected_error", [
    ({"ENABLE_AUTO_REPLIES": "not-a-bool"}, "ENABLE_AUTO_REPLIES must be a boolean"),
    ({"UNKNOWN_KEY": True}, "Unsupported local config key 'UNKNOWN_KEY'"),
    ({"MRS_BASE_DIR": "/should/not/apply"}, "Unsupported local config key 'MRS_BASE_DIR'"),
    ({"X_API_BASE_URL": "https://api.x.com"}, "Unsupported local config key 'X_API_BASE_URL'"),
])
def test_local_config_validation_rejects_bad_values_and_cannot_override_paths_or_urls(
    tmp_path: Path, local_config: dict, expected_error: str,
) -> None:
    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            local_config=local_config,
        )
        state_before = (base_dir / "bot_state.json").read_bytes()
        result = run_cycle(base_dir, server)
        assert result.returncode != 0
        assert server.posts == []
        assert expected_error in result.stdout + result.stderr
        assert "LocalConfigError:" in result.stderr
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
                "MIN_SECONDS_BETWEEN_REPLIES": -99,
                "MAX_QUOTE_POSTS_PER_CHECK": 0,
                "STATE_BACKUP_COUNT": -1,
                "QUOTE_REPLY_DELAY_SECONDS": -1,
                "POST_SLEEP_MIN": 9000,
                "POST_SLEEP_MAX": 10,
                "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS": 1000,
                "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS": 60,
                "MEME_FALLBACK_HOUR": 99,
                "MEME_FALLBACK_MINUTE": 99,
            },
        )
        state_before = (base_dir / "bot_state.json").read_bytes()
        result = run_cycle(base_dir, server)
        assert result.returncode != 0
        assert server.posts == []
        assert (base_dir / "bot_state.json").read_bytes() == state_before
        combined = result.stdout + result.stderr
        assert "Invalid local config" in combined
        assert "MAX_MENTIONS_PER_CHECK must be between 5 and 100" in combined
        assert "QUOTE_LOOKUP_API_MAX_RESULTS must be between 10 and 100" in combined
        assert "HOT_POST_REPLY_SEARCH_API_MAX_RESULTS must be between 10 and 100" in combined
        assert "REPLY_CHECK_EVERY_SECONDS must be positive" in combined
        assert "MIN_SECONDS_BETWEEN_REPLIES must be positive" in combined
        assert "MAX_QUOTE_POSTS_PER_CHECK must be positive" in combined
        assert "STATE_BACKUP_COUNT must be non-negative" in combined
        assert "QUOTE_REPLY_DELAY_SECONDS must be non-negative" in combined
        assert "POST_SLEEP_MIN must be <= POST_SLEEP_MAX" in combined
        assert "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS must be <= MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS" in combined
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
    server = FakeApiServer({"tweets": {}, "quote_tweets": {"999": {"data": [{
        "id": "1000", "referenced_tweets": [{"type": "quoted", "id": "999"}],
    }]}}}).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={"next_reply_lane_priority": "quote", "recent_own_post_ids": ["999"], "last_reply_epoch": 0},
            local_config={"ENABLE_HOT_POST_REPLY_CHECKS": False},
        )
        (base_dir / "extra_quote_watch_post_ids.txt").write_text("not-a-post-id\n", encoding="utf-8")
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        assert server.openai_requests == []
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
        "grok_replies": ["I favour accountability."],
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
        assert len(server.openai_requests) == 1
        assert server.xai_requests == []
        state = read_json(base_dir / "bot_state.json")
        assert "911" in state["replied_to_quote_post_ids"]
        assert "912" not in state["replied_to_quote_post_ids"]

        state["last_reply_epoch"] = 0
        state["seen_quote_post_ids"] = ["911", "912"]
        state["replied_to_quote_post_ids"] = []
        state["skipped_quote_post_ids"] = []
        persist_test_runtime_state(base_dir, state)
        before_openai = len(server.openai_requests)
        scenario["grok_replies"] = ["Should not be used for already seen quote."]
        seen = run_cycle(base_dir, server)
        assert seen.returncode == 0, seen.stderr + seen.stdout
        assert len(server.openai_requests) == before_openai
        assert server.xai_requests == []
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
        assert server.openai_requests == []
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
            "grok_reply": "I value civil disagreement.",
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
            "id": "999",
            "author_id": "3010",
            "conversation_id": "999",
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
            "grok_reply": "I favour accountability.",
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

        assert fake_server_post_replies(server) == ["999"]
        quote_requests = quote_search_requests(server)
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
            "tweets": {
                "700": {
                    "id": "700",
                    "text": "A watched account post.",
                    "author_id": "12345",
                    "conversation_id": "700",
                }
            },
            "search_recent": replies,
            "grok_reply": "I favour individual choice.",
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
        search_requests = [r for r in server.requests if r["path"] == "/2/tweets/search/recent" and "conversation_id:" in r["query"].get("query", [""])[0]]
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
            "grok_reply": "Responsibility matters.",
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
                "Clarity matters.",
                "I value evidence.",
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
        assert state["last_seen_mention_id"] == "204"
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
            "tweets": {
                "700": {
                    "id": "700",
                    "text": "A watched account post.",
                    "author_id": "12345",
                    "conversation_id": "700",
                }
            },
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
            "tweets": {
                "700": {
                    "id": "700",
                    "text": "A watched account post.",
                    "author_id": "12345",
                    "conversation_id": "700",
                }
            },
            "search_recent": replies,
            "grok_reply": "I value evidence.",
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
        search_requests = [r for r in server.requests if r["path"] == "/2/tweets/search/recent" and "conversation_id:" in r["query"].get("query", [""])[0]]
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
        quote_request = quote_search_requests(server)[0]
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
            "id": "999",
            "author_id": "3030",
            "conversation_id": "999",
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
            "grok_reply": "I favour liberty.",
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
        assert state["quote_search_pagination_tokens"]["(quotes_of_tweet_id:900) -is:retweet"] == "30"
        assert fake_server_post_replies(server) == []

        second = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000002"})
        assert second.returncode == 0, second.stderr + second.stdout
        assert fake_server_post_replies(server) == ["999"]
        state = read_json(base_dir / "bot_state.json")
        assert state["quote_search_pagination_tokens"] == {}
        quote_requests = quote_search_requests(server)
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
        decisions = [
            event
            for event in events
            if event["event"] == "single_call_reply_decision"
        ]
        assert len(decisions) == 1
        assert decisions[0]["pipeline_status"] == "no_reply"
        assert decisions[0]["outcome_type"] == "editorial"
        assert decisions[0]["reason_code"] == "no_meaningful_content"
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
    assert len(fake_server.openai_requests) == 1
    assert fake_server.xai_requests == []
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
    assert len(fake_server.openai_requests) == 1
    assert fake_server.xai_requests == []
    payload = json.loads(fake_server.openai_requests[0]["input"])
    assert payload["lane"] == "quote_tweet"
    assert payload["identities"] == {
        "parent_post_id": "900",
        "root_post_id": "900",
        "subject_post_id": "900",
        "target_post_id": "910",
    }
    assert payload["visible_conversation"][-1]["post_id"] == "910"
    assert sum(
        turn["post_id"] == "910" for turn in payload["visible_conversation"]
    ) == 1
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
    fixed_epoch = 2_000_000_000
    london_date = datetime.fromtimestamp(
        fixed_epoch, ZoneInfo("Europe/London")
    ).strftime("%Y-%m-%d")
    base_dir = prepare_base_dir(
        tmp_path,
        state={
            "recent_own_post_ids": ["900"],
            "last_reply_epoch": 0,
            "daily_reply_date": london_date,
            "daily_reply_count": prior_count,
            "daily_replied_author_ids": ["310"] if prior_count else [],
            "daily_replied_author_counts": {"310": prior_count} if prior_count else {},
        },
        local_config={"ENABLE_HOT_POST_REPLY_CHECKS": False},
    )

    result = run_bot_command(
        base_dir,
        fake_server,
        "--test-cycle",
        extra_env={"MRS_FAKE_NOW_EPOCH": str(fixed_epoch)},
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert len(fake_server.posts) == expected_posts
    state = read_json(base_dir / "bot_state.json")
    if prior_count < 6:
        assert state["daily_replied_author_counts"]["310"] == prior_count + 1
    else:
        assert fake_server.openai_requests == []
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
    fixed_epoch = 2_000_000_000
    london_date = datetime.fromtimestamp(
        fixed_epoch, ZoneInfo("Europe/London")
    ).strftime("%Y-%m-%d")
    base_dir = prepare_base_dir(
        tmp_path,
        state={
            "daily_reply_date": london_date,
            "daily_reply_count": 6,
            "daily_replied_author_ids": ["240"],
            "daily_replied_author_counts": {"240": 6},
            "last_reply_epoch": 0,
        },
    )
    result = run_bot_command(
        base_dir,
        fake_server,
        "--test-cycle",
        extra_env={"MRS_FAKE_NOW_EPOCH": str(fixed_epoch)},
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.posts == []
    assert fake_server.openai_requests == []
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
            "grok_replies": ["Responsibility matters more than rhetoric."],
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
        assert server.openai_requests == []
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

        assert len(server.openai_requests) == 1
        assert server.xai_requests == []
        model_payload = json.loads(server.openai_requests[0]["input"])
        assert model_payload["visible_conversation"] == [
            {"post_id": "500", "role": "user", "text": capped_text},
            {"post_id": "510", "role": "user", "text": newer_text},
        ]
        assert json.dumps(model_payload, sort_keys=True).count(capped_text) == 1
        assert model_payload["identities"]["target_post_id"] == "510"
        assert model_payload["identities"]["root_post_id"] == "500"
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
            "grok_replies": ["I favour individual choice.", "Responsibility matters.", "I favour accountability.", "Fourth reply should not be used."],
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
        assert len(server.openai_requests) == 3
        assert server.xai_requests == []
        state = read_json(base_dir / "bot_state.json")
        assert state["daily_replied_author_counts"]["240"] == 3
        assert state["daily_replied_author_ids"] == ["240"]
        assert state["last_seen_mention_id"] == "103"
    finally:
        server.stop()


@pytest.mark.parametrize("fake_server", ["daily_cap.json"], indirect=True)
def test_daily_cap_skips_before_fetching_mentions(tmp_path: Path, fake_server: FakeApiServer) -> None:
    fixed_epoch = 2_000_000_000
    london_date = datetime.fromtimestamp(
        fixed_epoch, ZoneInfo("Europe/London")
    ).strftime("%Y-%m-%d")
    base_dir = prepare_base_dir(
        tmp_path,
        local_config={"MAX_AUTO_REPLIES_PER_DAY": 48},
        state={
            "daily_reply_date": london_date,
            "daily_reply_count": 48,
            "last_reply_epoch": 0,
        },
    )
    result = run_bot_command(
        base_dir,
        fake_server,
        "--test-cycle",
        extra_env={"MRS_FAKE_NOW_EPOCH": str(fixed_epoch)},
    )

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

    assert result.returncode == 3, result.stderr + result.stdout
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

        assert result.returncode == 3, result.stderr + result.stdout
        assert server.path_counts["/2/tweets"] == 1
        assert server.posts == []
        state = read_json(base_dir / "bot_state.json")
        assert state["last_seen_mention_id"] == "100"
        assert set(state["mention_pending_candidates"]) == {"100"}
        assert state["x_write_error_epochs"]
        assert state["x_error_epochs"] == []
    finally:
        server.stop()


@pytest.mark.parametrize("fake_server", ["reply_not_allowed_403.json"], indirect=True)
def test_reply_not_allowed_403_is_terminal_without_remote_write_barrier(
    tmp_path: Path,
    fake_server: FakeApiServer,
) -> None:
    base_dir = prepare_base_dir(tmp_path)
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    assert fake_server.path_counts["/2/tweets"] == 1
    assert fake_server.posts == []
    state = read_json(base_dir / "bot_state.json")
    assert "190" in state["replied_to_ids"]
    assert "190" not in state["mention_pending_candidates"]
    assert state["daily_reply_count"] == 0
    assert state["daily_replied_author_counts"] == {}
    assert state["daily_replied_author_ids"] == []
    assert state["x_error_epochs"] == []
    assert state["x_write_error_epochs"] == []
    evaluation = state["reply_evaluation_records"]["190"]
    assert evaluation == {
        "evaluated_epoch": evaluation["evaluated_epoch"],
        "lane": "mention",
        "outcome": "reply_not_permitted",
        "reason": "x_reply_not_permitted",
        "target_id": "190",
    }
    assert isinstance(evaluation["evaluated_epoch"], int)
    assert evaluation["evaluated_epoch"] > 0
    assert not (base_dir / "ambiguous_post_outcome.json").exists()
    assert not (base_dir / "confirmed_reply_receipt.json").exists()
    assert not (base_dir / "remote_write_transport_journal.json").exists()
    assert not (base_dir / "remote_write_transport_fence.json").exists()


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


def test_native_photo_uses_one_multimodal_responses_request(
    tmp_path: Path,
) -> None:
    scenario = {
        "mentions": [
            {
                "id": "100",
                "text": "@MrsMThatcher Just like Australia. https://t.co/photo",
                "author_id": "200",
                "conversation_id": "100",
                "created_at": "2026-08-26T10:00:00Z",
                "attachments": {"media_keys": ["3_100"]},
            }
        ],
        "mentions_extra": {
            "includes": {
                "media": [
                    {
                        "media_key": "3_100",
                        "type": "photo",
                        "url": "pending",
                    }
                ]
            }
        },
        "openai_reply": (
            "Clarity matters more than rhetoric."
        ),
    }
    server = FakeApiServer(scenario).start()
    try:
        photo_url = f"{server.url}/media/native.png"
        scenario["mentions_extra"]["includes"]["media"][0]["url"] = photo_url
        base_dir = prepare_base_dir(
            tmp_path,
            state={"last_reply_epoch": 0},
            local_config={
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "ENABLE_QUOTE_TWEET_CHECKS": False,
            },
        )

        result = run_bot_command(
            base_dir,
            server,
            "--test-cycle",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )

        assert result.returncode == 0, result.stderr + result.stdout
        assert len(server.openai_requests) == 1
        assert server.xai_requests == []
        request = server.openai_requests[0]
        assert request["model"] == "gpt-5.6-sol"
        assert request["reasoning"] == {"effort": "high"}
        assert request["temperature"] == 1
        assert request["max_output_tokens"] == 8192
        assert request["store"] is False
        assert "tools" not in request
        assert request["text"]["format"]["type"] == "json_schema"
        assert request["text"]["format"]["name"] == (
            "single_call_reply_decision"
        )
        assert request["text"]["format"]["strict"] is True
        assert hashlib.sha256(request["instructions"].encode("utf-8")).hexdigest() == (
            "c1e6145bd90b9811258e91b598ff695ab69900878ed7c03e9210676638377d67"
        )
        assert isinstance(request["input"], list)
        content = request["input"][0]["content"]
        assert [item["type"] for item in content] == [
            "input_text",
            "input_image",
        ]
        model_payload = json.loads(content[0]["text"])
        assert model_payload["visible_conversation"][-1]["post_id"] == "100"
        assert sum(
            turn["post_id"] == "100"
            for turn in model_payload["visible_conversation"]
        ) == 1
        assert model_payload["visual_description"] is None
        assert content[1]["image_url"].startswith("data:image/png;base64,")
        assert photo_url not in json.dumps(request, sort_keys=True)
        assert fake_server_post_replies(server) == ["100"]
        events = event_payloads(base_dir)
        decisions = [
            event
            for event in events
            if event.get("event") == "single_call_reply_decision"
        ]
        assert len(decisions) == 1
        assert decisions[0]["model_call_count"] == 1
        assert decisions[0]["supplied_image_count"] == 1
        assert not any(
            event.get("event") == "reply_visual_description"
            for event in events
        )
        logged = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(base_dir.glob("mrsMThatcher.log*"))
        )
        assert "data:image/" not in logged
        assert "iVBORw0KGgpmaXh0dXJl" not in logged
    finally:
        server.stop()


def test_material_photo_fetch_failure_is_operational_and_makes_no_model_call(
    tmp_path: Path,
) -> None:
    scenario = {
        "mentions": [
            {
                "id": "100",
                "text": "@MrsMThatcher This depends on the photograph.",
                "author_id": "200",
                "conversation_id": "100",
                "created_at": "2026-08-26T10:00:00Z",
                "attachments": {"media_keys": ["3_100"]},
            }
        ],
        "mentions_extra": {
            "includes": {
                "media": [
                    {
                        "media_key": "3_100",
                        "type": "photo",
                        "url": "pending",
                    }
                ]
            }
        },
        "media_responses": {"/media/missing.png": {"status": 404}},
    }
    server = FakeApiServer(scenario).start()
    try:
        scenario["mentions_extra"]["includes"]["media"][0]["url"] = (
            f"{server.url}/media/missing.png"
        )
        base_dir = prepare_base_dir(
            tmp_path,
            state={"last_reply_epoch": 0, "daily_reply_count": 0},
            local_config={
                "ENABLE_HOT_POST_REPLY_CHECKS": False,
                "ENABLE_QUOTE_TWEET_CHECKS": False,
            },
        )
        result = run_bot_command(
            base_dir,
            server,
            "--test-cycle",
            extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"},
        )

        assert result.returncode == 0, result.stderr + result.stdout
        assert server.openai_requests == []
        assert server.xai_requests == []
        assert server.posts == []
        state = read_json(base_dir / "bot_state.json")
        assert state["openai_error_epochs"] == []
        assert state["openai_api_cooldown_until_epoch"] == 0
        assert state["daily_reply_count"] == 0
        assert state["replied_to_ids"] == []
        assert state["pending_ai_reply_drafts"] == {}
        assert state["mention_pending_candidates"] == {}
        assert state["reply_evaluation_records"]["100"]["outcome"] == (
            "operational_failure"
        )
        assert state.get("author_evaluation_quarantines", {}) == {}
        events = event_payloads(base_dir)
        decisions = [
            event
            for event in events
            if event.get("event") == "single_call_reply_decision"
        ]
        assert len(decisions) == 1
        assert decisions[0]["pipeline_status"] == "operational_failure"
        assert decisions[0]["error_category"] == "image_input"
        assert decisions[0]["model_call_count"] == 0
        assert decisions[0]["outcome_type"] == "operational"
    finally:
        server.stop()


@pytest.mark.parametrize("fake_server", ["openai_failure.json"], indirect=True)
def test_openai_failure_records_operational_error_without_posting(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path)
    result = run_cycle(base_dir, fake_server)

    assert result.returncode == 0, result.stderr + result.stdout
    state = read_json(base_dir / "bot_state.json")
    assert len(state["openai_error_epochs"]) == 1
    assert state["x_error_epochs"] == []
    assert set(state["mention_pending_candidates"]) == {"200"}
    assert state.get("reply_evaluation_records", {}).get("200") is None
    # A provider 5xx is ambiguous execution, so the production transport must
    # not issue a second Responses request for the same candidate.
    assert len(fake_server.openai_requests) == 1
    assert fake_server.xai_requests == []
    assert fake_server.posts == []


@pytest.mark.parametrize("fake_server", ["openai_failure.json"], indirect=True)
def test_openai_cooldown_does_not_block_quote_image_posting(tmp_path: Path, fake_server: FakeApiServer) -> None:
    base_dir = prepare_base_dir(tmp_path)

    for offset in [0, 10, 20]:
        result = run_bot_command(
            base_dir,
            fake_server,
            "--test-cycle",
            extra_env={"MRS_FAKE_NOW_EPOCH": str(2_000_000_000 + offset)},
        )
        assert result.returncode == 0, result.stderr + result.stdout

    state = read_json(base_dir / "bot_state.json")
    assert len(state["openai_error_epochs"]) == 3
    assert state["openai_api_cooldown_reason"] == (
        "too many openai API errors in the last hour"
    )
    assert int(state["openai_api_cooldown_until_epoch"]) > 2_000_000_000
    assert state["api_cooldown_until_epoch"] == 0
    assert fake_server.xai_requests == []

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
            "OPENAI_API_BASE_URL": "https://api.openai.com:443/v1",
        },
    )

    assert result.returncode == 2
    assert "X_API_BASE_URL=https://api.x.com:443" in result.stdout
    assert "X_UPLOAD_BASE_URL=https://upload.twitter.com:443" in result.stdout
    assert "OPENAI_API_BASE_URL=https://api.openai.com:443/v1" in result.stdout


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
            "OPENAI_API_BASE_URL": f"{fake}/v1",
        },
    )
    assert live_x.returncode == 2
    assert "X_API_BASE_URL=https://api.x.com" in live_x.stdout

    inherited_upload = run_bot_with_env(
        base_dir,
        "--self-test",
        extra_env={
            "X_API_BASE_URL": fake,
            "OPENAI_API_BASE_URL": f"{fake}/v1",
        },
    )
    assert inherited_upload.returncode == 0, (
        inherited_upload.stderr + inherited_upload.stdout
    )

    live_openai = run_bot_with_env(
        base_dir,
        "--self-test",
        extra_env={
            "X_API_BASE_URL": fake,
            "X_UPLOAD_BASE_URL": fake,
        },
    )
    assert live_openai.returncode == 2
    assert "OPENAI_API_BASE_URL=https://api.openai.com/v1" in live_openai.stdout


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
            "OPENAI_API_BASE_URL": f"{fake}/v1",
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
            "OPENAI_API_BASE_URL": f"{fake}/v1",
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
        ("closed", "openai", "/v1/responses"),
        ("timeout", "openai", "/v1/responses"),
    ],
)
def test_network_level_failures_closed_and_timeout(tmp_path: Path, failure: str, service: str, path: str) -> None:
    scenario = {"network_failures": {path: failure}, "network_timeout_sleep_seconds": 1}
    if service == "openai":
        scenario["mentions"] = load_scenario(SCENARIOS / "normal_mention_reply.json")["mentions"]
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(tmp_path)
        result = run_bot_command(base_dir, server, extra_env={"MRS_REQUEST_TIMEOUT_SECONDS": "0.05"})
        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        if service == "x":
            assert len(state["x_error_epochs"]) == 1
            assert state["openai_error_epochs"] == []
        else:
            assert len(state["openai_error_epochs"]) == 1
            assert state["x_error_epochs"] == []
            assert server.xai_requests == []
        assert server.posts == []
    finally:
        server.stop()


def test_connection_refused_for_x_and_openai_are_recorded(tmp_path: Path) -> None:
    x_base = prepare_base_dir(tmp_path / "x")
    x_result = run_bot_with_env(
        x_base,
        extra_env={
            "X_API_BASE_URL": "http://127.0.0.1:9",
            "X_UPLOAD_BASE_URL": "http://127.0.0.1:9",
            "XAI_API_BASE_URL": "http://127.0.0.1:9/v1",
            "OPENAI_API_BASE_URL": "http://127.0.0.1:9/v1",
            "MRS_REQUEST_TIMEOUT_SECONDS": "0.05",
        },
    )
    assert x_result.returncode == 0, x_result.stderr + x_result.stdout
    assert len(read_json(x_base / "bot_state.json")["x_error_epochs"]) == 1

    server = FakeApiServer(load_scenario(SCENARIOS / "normal_mention_reply.json")).start()
    try:
        openai_base = prepare_base_dir(tmp_path / "openai")
        openai_result = run_bot_command(
            openai_base,
            server,
            openai_api_base_url="http://127.0.0.1:9/v1",
            extra_env={"MRS_REQUEST_TIMEOUT_SECONDS": "0.05"},
        )
        assert openai_result.returncode == 0, (
            openai_result.stderr + openai_result.stdout
        )
        state = read_json(openai_base / "bot_state.json")
        assert len(state["openai_error_epochs"]) == 1
        assert state["x_error_epochs"] == []
        assert server.xai_requests == []
    finally:
        server.stop()


@pytest.mark.parametrize(
    "scenario_update",
    [
        {"openai_non_json": True},
        {"openai_responses": [{"body": {}}]},
        {
            "openai_responses": [{
                "body": {
                    "status": "completed",
                    "model": "gpt-5.6-sol",
                    "output": [],
                }
            }]
        },
        {
            "openai_reply_decisions": [""],
        },
    ],
)
def test_malformed_openai_responses_are_operational_failures(
    tmp_path: Path,
    scenario_update: dict,
) -> None:
    scenario = load_scenario(SCENARIOS / "normal_mention_reply.json")
    scenario.update(scenario_update)
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(tmp_path)
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        assert bool(state["openai_error_epochs"])
        assert state["last_seen_mention_id"] == "100"
        assert set(state["mention_pending_candidates"]) == {"100"}
        assert state.get("reply_evaluation_records", {}) == {}
        assert server.posts == []
        assert server.xai_requests == []
    finally:
        server.stop()


def test_completed_locally_invalid_response_is_retired_and_later_candidate_runs(
    tmp_path: Path,
) -> None:
    scenario = load_scenario(SCENARIOS / "normal_mention_reply.json")
    scenario["mentions"].append(
        {
            "id": "101",
            "text": "@mrsMThatcher a later candidate",
            "author_id": "201",
            "conversation_id": "101",
            "created_at": "2026-06-30T12:01:00Z",
        }
    )
    scenario["openai_reply_decisions"] = [
        {
            "decision": "reply",
            "reply_kind": "social",
            "reply": "word " * 200,
            "used_fact_ids": [], "factual_claims": [],
            "reason_code": "useful_reply",
        },
        {
            "decision": "no_reply",
            "reply_kind": "no_reply",
            "reply": "",
            "used_fact_ids": [], "factual_claims": [],
            "reason_code": "completed_exchange",
        },
    ]
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(tmp_path)
        first = run_cycle(base_dir, server)
        assert first.returncode == 0, first.stderr + first.stdout
        state = read_json(base_dir / "bot_state.json")
        assert len(server.openai_requests) == 2
        assert server.posts == []
        assert state["openai_error_epochs"] == []
        assert state["openai_api_cooldown_until_epoch"] == 0
        assert state["reply_evaluation_records"]["100"]["outcome"] == (
            "operational_failure"
        )
        assert state["reply_evaluation_records"]["101"]["outcome"] == "no_reply"
        assert state["mention_pending_candidates"] == {}
        assert state["daily_reply_count"] == 0
        assert state["daily_quote_reply_count"] == 0
        assert state["author_evaluation_quarantines"] == {}

        second = run_cycle(base_dir, server)
        assert second.returncode == 0, second.stderr + second.stdout
        assert len(server.openai_requests) == 2
    finally:
        server.stop()


def test_quote_tweet_malformed_openai_response_records_operational_error(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    scenario["openai_responses"] = [{"body": {}}]
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
        assert len(state["openai_error_epochs"]) == 1
        assert state["x_error_epochs"] == []
        assert server.xai_requests == []
    finally:
        server.stop()


def test_quote_tweet_local_validation_failure_is_terminal_not_provider_health(
    tmp_path: Path,
) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    scenario["openai_reply_decisions"] = [
        {
            "decision": "reply",
            "reply_kind": "social",
            "reply": "word " * 200,
            "used_fact_ids": [], "factual_claims": [],
            "reason_code": "useful_reply",
        }
    ]
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "next_reply_lane_priority": "quote",
                "recent_own_post_ids": ["900"],
                "last_reply_epoch": 0,
            },
            local_config={"ENABLE_HOT_POST_REPLY_CHECKS": False},
        )
        first = run_cycle(base_dir, server)

        assert first.returncode == 0, first.stderr + first.stdout
        assert server.posts == []
        assert len(server.openai_requests) == 1
        state = read_json(base_dir / "bot_state.json")
        assert state["openai_error_epochs"] == []
        assert state["openai_api_cooldown_until_epoch"] == 0
        assert state["reply_evaluation_records"]["910"]["outcome"] == (
            "operational_failure"
        )
        assert "910" in state["skipped_quote_post_ids"]
        assert state["daily_reply_count"] == 0
        assert state["daily_quote_reply_count"] == 0

        second = run_cycle(base_dir, server)
        assert second.returncode == 0, second.stderr + second.stdout
        assert len(server.openai_requests) == 1
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
        assert "## Single-call conversational replies" in digest.stdout
        assert "1 candidate evaluated; 1 reply posted" in digest.stdout
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
        assert "single_call_reply_decision" in (
            skip_base / "test.log"
        ).read_text(encoding="utf-8")
        assert "1 valid editorial no-reply decision" in digest.stdout
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
    assert "current API cooldown state unavailable" in ops_digest.stdout
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
                "2026-07-03 10:31:00 DEBUG    save_state:994 - State being saved: {\"api_cooldown_until_epoch\": 0, \"openai_api_cooldown_until_epoch\": 0, \"quote_api_cooldown_until_epoch\": 0}",
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
        "\"openai_api_cooldown_until_epoch\": 4102444800, "
        "\"openai_api_cooldown_reason\": \"too many openai API errors in the last hour\", "
        "\"quote_api_cooldown_until_epoch\": 0}\n",
        encoding="utf-8",
    )
    write_json(
        xai_cooldown_base / "bot_state.json",
        {
            "daily_reply_count": 0,
            "api_cooldown_until_epoch": 0,
            "x_write_api_cooldown_until_epoch": 0,
            "openai_api_cooldown_until_epoch": 4102444800,
            "openai_api_cooldown_reason": "too many openai API errors in the last hour",
            "quote_api_cooldown_until_epoch": 0,
        },
    )
    xai_cooldown_digest = run_digest(xai_cooldown_base)
    assert xai_cooldown_digest.returncode == 0, xai_cooldown_digest.stderr
    assert "OpenAI cooldown active now" in xai_cooldown_digest.stdout
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
            "x_write_api_cooldown_until_epoch": 0,
            "openai_api_cooldown_until_epoch": 0,
            "quote_api_cooldown_until_epoch": 0,
        },
    )
    stale_digest = run_digest(stale_base)
    assert stale_digest.returncode == 0, stale_digest.stderr
    assert "X read API cooldown occurred, now expired" in stale_digest.stdout
    assert "no API cooldown" not in stale_digest.stdout
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
                "2026-07-03 11:00:00 DEBUG    save_state:994 - State being saved: {\"api_cooldown_until_epoch\": 0, \"api_cooldown_reason\": \"\", \"quote_api_cooldown_until_epoch\": 0, \"quote_api_cooldown_reason\": \"\", \"x_write_api_cooldown_until_epoch\": 0, \"x_write_api_cooldown_reason\": \"\", \"openai_api_cooldown_until_epoch\": 0, \"openai_api_cooldown_reason\": \"\"}",
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
            "openai_api_cooldown_until_epoch": 0,
            "openai_api_cooldown_reason": "",
        },
    )
    cleared_digest = run_digest(cleared_base, state_file=cleared_state_file)
    assert cleared_digest.returncode == 0, cleared_digest.stderr
    assert "x_read_api_cooldown_until = 0  none" in cleared_digest.stdout
    assert "x_write_api_cooldown_until = 0  none" in cleared_digest.stdout
    assert "quote_api_cooldown_until = 0  none" in cleared_digest.stdout
    assert "no API cooldown" in cleared_digest.stdout
    current_state_section, historical_section = cleared_digest.stdout.split(
        "## Historical retained diagnostic snapshots", 1
    )
    assert "2026-07-03 05:55:42" not in current_state_section
    assert "old cooldown" not in current_state_section
    assert "old write cooldown" not in current_state_section
    assert "2026-07-03 05:55:42" in historical_section
    assert "old cooldown" in historical_section
    assert "old write cooldown" in historical_section


def test_digest_archived_question_post_keeps_confirmed_public_text(
    tmp_path: Path,
) -> None:
    base = tmp_path / "digest-engagement-treatment"
    fixture = write_treatment_quote_digest_fixture(base)

    digest = run_digest(base)

    assert digest.returncode == 0, digest.stderr
    quote_section = digest_markdown_section(digest.stdout, "Quote/image posts")
    quote_row = next(
        line
        for line in quote_section.splitlines()
        if f"| {fixture['post_id']} |" in line
    )
    assert "| 398 |" in quote_row
    assert f"| {fixture['quote_hash']} | t12.jpg | 11 | 25.4 |" in quote_row
    assert str(fixture["public_text"]).replace("\n", "\\n") in quote_row
    assert "t.co" not in quote_row
    assert "Engagement-question trial" not in digest.stdout
    result = run_digest(base, as_json=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    posted, = [row for row in payload["events"] if row["kind"] == "quote_image_posted"]
    assert posted["public_text"] == fixture["public_text"]
    assert not any(key.startswith("engagement_") for key in posted)
    assert "engagement_question_trial" not in payload


def test_digest_quote_image_ordinary_post_uses_account_root_text(
    tmp_path: Path,
) -> None:
    base = tmp_path / "digest-ordinary-quote-root-text"
    post_id = "2094000000000000002"
    quote_hash = "c" * 64
    quote_text = "An ordinary | quotation keeps its exact public text."
    write_digest_log(
        base,
        [
            "2026-08-30 20:00:00 INFO     select_quote_candidate:4630 - "
            f"Selected quote line_no=22 quote_hash={quote_hash} "
            "weight=1.00 seasonal_boost=False",
            "2026-08-30 20:00:01 INFO     choose_matched_unused_image:4931 - "
            "Selected matched image basename=t03.jpg image_no=2 score=8.0 "
            "components=historical=4.0",
            digest_event_line(
                "2026-08-30 20:00:02",
                "main_post_posted",
                lane="quote_image",
                post_id=post_id,
                line_no=22,
                image_no=2,
                image_basename="t03.jpg",
                image_score=8.0,
                quote_hash=quote_hash,
            ),
            digest_event_line(
                "2026-08-30 20:00:03",
                "account_root_posted",
                event_version=1,
                lane="quote_image",
                post_id=post_id,
                root_post_id=post_id,
                conversation_id=post_id,
                quote_id=quote_hash,
                quote_text=quote_text,
                public_text=quote_text,
                visible_text_source="public_text",
                publication_authority="confirmed_transport",
            ),
            "2026-08-30 20:00:04 INFO     post_random_quote:5198 - "
            f"Quote/image posted successfully. posted_id={post_id}",
        ],
    )

    digest = run_digest(base)

    assert digest.returncode == 0, digest.stderr
    quote_section = digest_markdown_section(digest.stdout, "Quote/image posts")
    quote_row = next(
        line for line in quote_section.splitlines() if f"| {post_id} |" in line
    )
    assert "An ordinary \\| quotation keeps its exact public text." in quote_row
    assert quote_row.endswith(
        "| An ordinary \\| quotation keeps its exact public text. |"
    )
    assert "## Engagement-question trial activity" not in digest.stdout


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


def test_digest_json_media_upload_incident_has_correlated_source_refs(
    tmp_path: Path,
) -> None:
    base = tmp_path / "digest-media-fallback-provenance"
    media_file = "/tmp/provenance.png"
    write_digest_log(
        base,
        [
            f"2026-07-06 15:20:24 INFO     upload_media_v2:2970 - Uploading media via X API v2: {media_file}",
            "2026-07-06 15:21:23 ERROR    x_request:1967 - X request failed before receiving response\nrequests.exceptions.ReadTimeout: timed out",
            "2026-07-06 15:21:24 ERROR    upload_media:3054 - v2 media upload failed; trying v1.1 fallback",
            "2026-07-06 15:21:25 INFO     upload_media_v1_1:3043 - Uploaded media via v1.1. media_id=2074136638502866945",
            "2026-07-06 15:21:26 INFO     post_random_quote:5281 - Quote/image posted successfully. posted_id=2074136641040499171",
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    incident = payload["media_upload"]["incidents"][0]
    assert incident["status"] == "handled"
    assert "source_ref_omitted_count" not in incident
    assert incident["source_refs"] == [
        {
            "input_file_index": 0,
            "record_number": 3,
            "timestamp": "2026-07-06 15:21:24",
            "logger": "upload_media",
            "logged_source_line_number": 3054,
        },
        {
            "input_file_index": 0,
            "record_number": 2,
            "timestamp": "2026-07-06 15:21:23",
            "logger": "x_request",
            "logged_source_line_number": 1967,
        },
        {
            "input_file_index": 0,
            "record_number": 4,
            "timestamp": "2026-07-06 15:21:25",
            "logger": "upload_media_v1_1",
            "logged_source_line_number": 3043,
        },
        {
            "input_file_index": 0,
            "record_number": 5,
            "timestamp": "2026-07-06 15:21:26",
            "logger": "post_random_quote",
            "logged_source_line_number": 5281,
        },
    ]
    assert Path(payload["input_files"][0]["path"]).name == "test.log"


def test_digest_json_media_upload_incident_bounds_correlated_source_refs(
    tmp_path: Path,
) -> None:
    base = tmp_path / "digest-media-fallback-provenance-bound"
    media_file = "/tmp/provenance-bound.png"
    failures = [
        (
            f"2026-07-06 15:21:{16 + index:02d} ERROR    "
            f"x_request:{1960 + index} - X request failed before receiving response"
        )
        for index in range(8)
    ]
    write_digest_log(
        base,
        [
            f"2026-07-06 15:20:24 INFO     upload_media_v2:2970 - Uploading media via X API v2: {media_file}",
            *failures,
            "2026-07-06 15:21:24 ERROR    upload_media:3054 - v2 media upload failed; trying v1.1 fallback",
            "2026-07-06 15:21:25 INFO     upload_media_v1_1:3043 - Uploaded media via v1.1. media_id=2074136638502866945",
            "2026-07-06 15:21:26 INFO     post_random_quote:5281 - Quote/image posted successfully. posted_id=2074136641040499171",
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    incident = json.loads(result.stdout)["media_upload"]["incidents"][0]
    assert incident["status"] == "handled"
    assert len(incident["source_refs"]) == 8
    assert incident["source_ref_omitted_count"] == 3
    assert incident["source_refs"][0] == {
        "input_file_index": 0,
        "record_number": 10,
        "timestamp": "2026-07-06 15:21:24",
        "logger": "upload_media",
        "logged_source_line_number": 3054,
    }
    assert [
        source_ref["record_number"]
        for source_ref in incident["source_refs"][1:]
    ] == list(range(2, 9))


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


def test_digest_confirmed_reply_receipt_lifecycle_is_source_isolated(
    tmp_path: Path,
) -> None:
    base = tmp_path / "digest-confirmed-reply-source-isolation"
    base.mkdir()
    production_log = base / "application.log"
    selftest_log = base / "application.selftest.log"
    production_log.write_text(
        "\n".join(
            [
                "2026-07-07 06:02:11 WARNING reconcile_confirmed_reply_receipt:5690 - "
                "Reconciling confirmed reply receipt source=mention "
                "target_id=123 reply_post_id=999",
                "2026-07-07 06:02:14 INFO remove_confirmed_reply_receipt:5621 - "
                "Removed reconciled confirmed-reply receipt",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    selftest_log.write_text(
        "\n".join(
            [
                "2026-07-07 06:02:12 INFO remove_confirmed_reply_receipt:5621 - "
                "Removed reconciled confirmed-reply receipt",
                "2026-07-07 06:02:13 WARNING write_confirmed_reply_receipt:5614 - "
                "Wrote confirmed reply receipt pending local reconciliation "
                "source=quote_tweet target_id=456 reply_post_id=888",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_digest_inputs(base, [production_log, selftest_log])

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert any(
        "Unresolved confirmed reply receipt reconciliation" in item["message"]
        for item in payload["errors_and_warnings"]
    ) is False
    production_removed = next(
        item
        for item in payload["confirmed_reply_recovery"]["receipt_events"]
        if item["kind"] == "removed"
        and item["source_class"] == "production"
    )
    assert production_removed.get("lane", "") == "mention"
    assert production_removed.get("target_id", "") == "123"
    assert production_removed.get("reply_post_id", "") == "999"


def test_digest_selftest_receipt_removal_cannot_clear_production_incident(
    tmp_path: Path,
) -> None:
    base = tmp_path / "digest-confirmed-reply-selftest-removal"
    base.mkdir()
    production_log = base / "application.log"
    selftest_log = base / "application.selftest.log"
    production_log.write_text(
        "2026-07-07 06:02:11 WARNING reconcile_confirmed_reply_receipt:5690 - "
        "Reconciling confirmed reply receipt source=mention "
        "target_id=123 reply_post_id=999\n",
        encoding="utf-8",
    )
    selftest_log.write_text(
        "2026-07-07 06:02:12 INFO remove_confirmed_reply_receipt:5621 - "
        "Removed reconciled confirmed-reply receipt\n",
        encoding="utf-8",
    )

    result = run_digest_inputs(base, [production_log, selftest_log])

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert any(
        "Unresolved confirmed reply receipt reconciliation" in item["message"]
        for item in payload["errors_and_warnings"]
    )


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
    assert "## Historical generated image spacing" in digest.stdout
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


def test_digest_json_source_identity_is_stable_across_resume_filtering(
    tmp_path: Path,
) -> None:
    base = tmp_path / "digest-provenance-resume-boundary"
    state_file = tmp_path / "digest-provenance-state.json"
    boundary = digest_event_line(
        "2026-07-08 06:39:38",
        "mention_backlog_started",
        pages_completed=1,
        since_id="100",
    )
    retained = digest_event_line(
        "2026-07-08 06:39:38",
        "mention_backlog_progress",
        pages_completed=2,
        since_id="100",
    )
    write_digest_log(base, [boundary])
    initial = run_digest(base, state_file=state_file, as_json=True)
    assert initial.returncode == 0, initial.stderr

    write_digest_log(base, [boundary, retained])
    no_state = run_digest(base, as_json=True)
    resumed = run_digest(base, state_file=state_file, as_json=True)

    assert no_state.returncode == 0, no_state.stderr
    assert resumed.returncode == 0, resumed.stderr
    no_state_payload = json.loads(no_state.stdout)
    resumed_payload = json.loads(resumed.stdout)

    def progress_source(payload: dict) -> dict:
        event = next(
            item
            for item in payload["events"]
            if item.get("kind") == "mention_backlog_progress"
        )
        assert len(event["source_refs"]) == 1
        return event["source_refs"][0]

    expected_source = {
        "input_file_index": 0,
        "record_number": 2,
        "timestamp": "2026-07-08 06:39:38",
        "logger": "log_event",
        "logged_source_line_number": 330,
    }
    assert progress_source(no_state_payload) == expected_source
    assert progress_source(resumed_payload) == expected_source
    assert resumed_payload["resume_cursor_mode"] == "fingerprint_tail"
    assert resumed_payload["resume_tail_match_length"] == 1
    assert resumed_payload["summary"]["record_count"] == 1
    assert Path(no_state_payload["input_files"][0]["path"]).name == "test.log"
    assert (
        resumed_payload["input_files"][0]["path"]
        == no_state_payload["input_files"][0]["path"]
    )


def test_digest_json_contract_identifies_the_major_versioned_schema_and_retained_roots(
    tmp_path: Path,
) -> None:
    base = tmp_path / "digest-json-contract"
    write_digest_log(
        base,
        ["2026-08-30 20:00:00 INFO     main:1 - Main loop tick"],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    contract = payload["digest_contract"]
    assert contract["schema_version"] == 4
    assert contract["output_kind"] == "mrs_log_digest"
    assert contract["producer"] == "mrs_log_digest.py"
    assert contract["compatibility_policy"] == "major-versioned"
    source_hash = contract["producer_source_sha256"]
    assert source_hash == hashlib.sha256(DIGEST.read_bytes()).hexdigest()
    assert len(source_hash) == 64
    assert source_hash == source_hash.lower()
    assert set(source_hash) <= set("0123456789abcdef")
    repository_sha = contract["repository_head_sha"]
    assert repository_sha is None or (
        40 <= len(repository_sha) <= 64
        and repository_sha == repository_sha.lower()
        and set(repository_sha) <= set("0123456789abcdef")
    )
    assert "generator" + "_git_sha" not in contract
    projection_semantics = contract["projection_semantics"]
    assert set(projection_semantics) == {
        "latest_state",
        "latest_config",
        "historical_retained_state",
        "historical_retained_config",
    }
    assert all(
        "projection" in str(description).lower()
        for description in projection_semantics.values()
    )

    retained_top_level_keys = {
        "summary",
        "latest_config",
        "latest_state",
        "derived",
        "mention_backlog_and_quarantine",
        "api_health",
        "main_post_recovery",
        "remote_write_transactions",
        "confirmed_reply_recovery",
        "quote_publication",
        "historical_context_replies",
        "production_consistency",
        "historical_context_quality",
        "single_call_reply",
        "legacy_multi_stage",
        "asset_health",
        "media_upload",
        "regular_image_usage",
        "original_editorial_shadow",
        "generated_identity_shadow",
        "generated_identity_policy",
        "generated_image_spacing",
        "resume_context",
        "lifecycle",
        "events",
        "self_test_errors",
        "error_health",
        "errors_and_warnings",
        "generation_time",
        "generation_epoch",
        "remote_write_safety",
        "log_files",
        "input_files",
        "input_warning",
        "input_retention_coverage",
        "requested_since",
        "requested_until",
        "since_source",
        "since_exclusive",
        "resume_cursor_mode",
        "resume_tail_match_length",
        "local_clock_rollback_count",
        "resume_boundary_fingerprint_count",
        "resume_boundary_occurrence_count",
        "project_dir",
        "resume_state_file",
        "state_updated",
        "generated_image_pool_health",
        "historical_context_corpus_snapshot",
        "historical_context_engagement",
        "shadow_feature_lifecycle",
        "runtime_state_status",
        "runtime_config_status",
        "current_cooldown_status",
        "generated_image_post_rates",
        "generated_image_pool_runway",
        "generated_image_utilisation",
        "verbose_replies",
        "detailed_appendix",
        "openai_published_cost",
    }
    assert retained_top_level_keys <= set(payload)
    assert {
        "reply_strategy",
        "reply_pipeline_stages",
        "reply_media_context",
        "reply_visual_context_summary",
        "reply_visual_context_targets",
        "provider_usage",
        "xai_usage",
    }.isdisjoint(payload)


def test_copied_digest_without_git_still_emits_valid_contract_json(
    tmp_path: Path,
) -> None:
    isolated = tmp_path / "outside-git"
    isolated.mkdir()
    copied_script = isolated / "mrs_log_digest.py"
    copied_script.write_bytes(DIGEST.read_bytes())
    log = isolated / "test.log"
    log.write_text(
        "2026-08-30 20:00:00 INFO     main:1 - Main loop tick\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(isolated / "empty-home"),
            "PATH": "",
            "PYTHONPATH": str(ROOT),
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            str(copied_script),
            "--project-dir",
            str(isolated),
            "--no-state",
            "--json",
            str(log),
        ],
        cwd=isolated,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    contract = payload["digest_contract"]
    assert contract["schema_version"] == 4
    assert contract["producer_source_sha256"] == hashlib.sha256(
        copied_script.read_bytes()
    ).hexdigest()
    assert contract["repository_head_sha"] is None
    assert "generator" + "_git_sha" not in contract


def test_digest_historical_context_reply_uses_exact_confirmed_text_and_bounded_provenance(
    tmp_path: Path,
) -> None:
    base = tmp_path / "historical-context-exact-text"
    base.mkdir()
    active = base / "mrsMThatcher.log"
    rotated = base / "mrsMThatcher.log.1"
    parent_id = "2095000000000000001"
    reply_id = "2095000000000000002"
    quote_id = "a" * 64
    preview = "Context — a short preview…"
    exact_text = (
        "Context — The confirmed first line.\n"
        "\n"
        "Meaning — The confirmed second line preserves spacing."
    )
    rotated.write_text(
        digest_event_line(
            "2026-08-30 20:00:00",
            "historical_context_reply",
            status="completed",
            parent_post_id=parent_id,
            quote_id=quote_id,
            character_count=len(exact_text),
            reply_preview=preview,
        )
        + "\n",
        encoding="utf-8",
    )
    active.write_text(
        "2026-08-30 20:00:01 INFO     create_post:901 - Created X post "
        f"successfully. response={{'data': {{'id': '{reply_id}', "
        "'text': 'Context — transport preview…'}}}}\n"
        + digest_event_line(
            "2026-08-30 20:00:02",
            "historical_context_reply_posted",
            event_version=1,
            lane="historical_context_reply",
            parent_post_id=parent_id,
            reply_post_id=reply_id,
            root_post_id=parent_id,
            conversation_id=parent_id,
            reply_text=exact_text,
            quote_id=quote_id,
            reply_created_at="2026-08-30T19:00:02Z",
            publication_authority="confirmed_transport",
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(DIGEST),
            "--project-dir",
            str(base),
            "--no-state",
            "--json",
            str(active),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    historical = payload["historical_context_replies"]["events"]
    assert len(historical) == 1
    reply = historical[0]
    assert reply["reply_preview"] == preview
    assert reply["parent_post_id"] == parent_id
    assert reply["reply_post_id"] == reply_id
    assert reply["public_reply_text"] == exact_text
    assert reply["public_reply_text_sha256"] == hashlib.sha256(
        exact_text.encode("utf-8")
    ).hexdigest()
    assert reply["public_reply_text_character_count"] == len(exact_text)
    assert reply["public_reply_text_complete"] is True
    assert reply["public_reply_text_status"] == "confirmed"
    assert "historical_context_reply_posted" in reply["public_reply_text_source"]
    assert reply["public_reply_text_reason"] == ""
    assert reply["correlation_status"] == "exact"
    assert reply["public_reply_text"] != "Context — transport preview…"

    root_reply = next(
        event
        for event in payload["events"]
        if event.get("kind") == "historical_context_reply"
    )
    for field in (
        "reply_post_id",
        "parent_post_id",
        "public_reply_text",
        "public_reply_text_sha256",
        "public_reply_text_character_count",
        "public_reply_text_complete",
        "public_reply_text_status",
        "public_reply_text_source",
        "public_reply_text_reason",
        "correlation_status",
    ):
        assert root_reply[field] == reply[field]

    assert "source_ref_omitted_count" not in reply
    assert len(reply["source_refs"]) == 2
    input_names = {
        index: Path(item["path"]).name
        for index, item in enumerate(payload["input_files"])
    }
    assert {
        input_names[source_ref["input_file_index"]]
        for source_ref in reply["source_refs"]
    } == {"mrsMThatcher.log", "mrsMThatcher.log.1"}
    assert {source_ref["timestamp"] for source_ref in reply["source_refs"]} == {
        "2026-08-30 20:00:00",
        "2026-08-30 20:00:02",
    }
    assert all(source_ref["record_number"] >= 1 for source_ref in reply["source_refs"])
    assert all(source_ref["logger"] == "log_event" for source_ref in reply["source_refs"])
    assert all(
        source_ref["logged_source_line_number"] == 330
        for source_ref in reply["source_refs"]
    )


def test_digest_confirmed_reply_lanes_use_authoritative_state_text_and_exclude_drafts(
    tmp_path: Path,
) -> None:
    base = tmp_path / "confirmed-reply-lanes"
    base.mkdir()
    replies = {
        "mention": ("1001", "9001", "Mention reply, complete and confirmed.\nSecond line."),
        "hot_post_reply": ("1002", "9002", "Hot-post reply, complete and confirmed."),
        "quote_tweet": ("1003", "9003", "Quote-tweet reply, complete and confirmed."),
    }
    state = {
        "daily_reply_count": 3,
        "tweet_cache": {},
        "ai_reply_history": [],
        "pending_ai_reply_drafts": {
            "mention:1999": {
                "target_id": "1999",
                "candidate_source": "mention",
                "proposed_reply": "Unconfirmed draft must not be published.",
            }
        },
    }
    for lane, (target_id, reply_id, text) in replies.items():
        state["tweet_cache"][reply_id] = {
            "id": reply_id,
            "author_id": "12345",
            "conversation_id": target_id,
            "created_at": "2026-08-30T19:10:00Z",
            "referenced_tweets": [{"type": "replied_to", "id": target_id}],
            "text": text,
            "cached_epoch": 1_788_120_600,
            "post_type": "auto_reply",
        }
        state["ai_reply_history"].append(
            {
                "target_id": target_id,
                "reply_post_id": reply_id,
                "candidate_source": lane,
                "reply_epoch": 1_788_120_600,
                "proposed_reply": text,
            }
        )
    write_json(base / "bot_state.json", state)
    mention_target, mention_reply, _mention_text = replies["mention"]
    hot_target, hot_reply, _hot_text = replies["hot_post_reply"]
    quote_target, quote_reply, _quote_text = replies["quote_tweet"]
    write_digest_log(
        base,
        [
            f"2026-08-30 20:10:00 INFO maybe_reply_to_mentions:100 - Considering mention id={mention_target} author_id=501 text='Mention input'",
            f"2026-08-30 20:10:01 INFO maybe_reply_to_mentions:101 - Generated reply to mention {mention_target}: 'Short mention draft.'",
            f"2026-08-30 20:10:02 INFO create_post:102 - Created X post successfully. response={{'data': {{'id': '{mention_reply}'}}}}",
            f"2026-08-30 20:10:03 INFO maybe_reply_to_mentions:103 - Recorded and cached own auto-reply id={mention_reply}",
            "2026-08-30 20:10:04 INFO maybe_reply_to_mentions:104 - Reply posted successfully",
            digest_event_line(
                "2026-08-30 20:10:05",
                "reply_posted",
                lane="mention+hot_post_reply",
                target_id=mention_target,
                reply_post_id=mention_reply,
                author_id="501",
            ),
            f"2026-08-30 20:11:00 INFO maybe_reply_to_mentions:110 - Considering hot_post_reply id={hot_target} author_id=502 text='Hot input'",
            f"2026-08-30 20:11:01 INFO maybe_reply_to_mentions:111 - Generated reply to mention {hot_target}: 'Short hot draft.'",
            f"2026-08-30 20:11:02 INFO create_post:112 - Created X post successfully. response={{'data': {{'id': '{hot_reply}'}}}}",
            f"2026-08-30 20:11:03 INFO maybe_reply_to_mentions:113 - Recorded and cached own auto-reply id={hot_reply}",
            "2026-08-30 20:11:04 INFO maybe_reply_to_mentions:114 - Reply posted successfully",
            digest_event_line(
                "2026-08-30 20:11:05",
                "reply_posted",
                lane="hot_post_reply",
                target_id=hot_target,
                reply_post_id=hot_reply,
                author_id="502",
            ),
            f"2026-08-30 20:12:00 INFO maybe_reply_to_quote_tweets:120 - Considering quote tweet id={quote_target} author_id=503 original_post_id=7001 text='Quote input'",
            f"2026-08-30 20:12:01 INFO maybe_reply_to_quote_tweets:121 - Generated reply to quote tweet {quote_target}: 'Short quote draft.'",
            f"2026-08-30 20:12:02 INFO create_post:122 - Created X post successfully. response={{'data': {{'id': '{quote_reply}'}}}}",
            f"2026-08-30 20:12:03 INFO maybe_reply_to_quote_tweets:123 - Recorded and cached own quote-tweet auto-reply id={quote_reply}",
            "2026-08-30 20:12:04 INFO maybe_reply_to_quote_tweets:124 - Quote-tweet reply posted successfully",
            digest_event_line(
                "2026-08-30 20:12:05",
                "reply_posted",
                lane="quote_tweet",
                target_id=quote_target,
                reply_post_id=quote_reply,
                author_id="503",
                original_post_id="7001",
            ),
            "2026-08-30 20:13:00 INFO maybe_reply_to_mentions:130 - Considering mention id=1999 author_id=504 text='Unconfirmed input'",
            "2026-08-30 20:13:01 INFO maybe_reply_to_mentions:131 - Generated reply to mention 1999: 'Unconfirmed draft must not be published.'",
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    expected_kinds = {
        "mention": "mention_reply_posted",
        "hot_post_reply": "hot_post_reply_posted",
        "quote_tweet": "quote_tweet_reply_posted",
    }
    for lane, (target_id, reply_id, text) in replies.items():
        event = next(
            item
            for item in payload["events"]
            if item.get("kind") == expected_kinds[lane]
        )
        target_field = {
            "mention": "mention_id",
            "hot_post_reply": "hot_post_reply_id",
            "quote_tweet": "quote_tweet_id",
        }[lane]
        assert event[target_field] == target_id
        assert event["reply_post_id"] == reply_id
        assert event["public_reply_text"] == text
        assert event["public_reply_text_sha256"] == hashlib.sha256(
            text.encode("utf-8")
        ).hexdigest()
        assert event["public_reply_text_character_count"] == len(text)
        assert event["public_reply_text_complete"] is True
        assert event["public_reply_text_status"] == "confirmed"
        assert "bot_state.json" in event["public_reply_text_source"]
        assert "tweet_cache" in event["public_reply_text_source"]
        assert event["public_reply_text_reason"] == ""
        assert event["correlation_status"] == "exact"

    assert not any(
        event.get("public_reply_text") == "Unconfirmed draft must not be published."
        for event in payload["events"]
    )


def test_digest_reply_text_correlation_uses_immutable_interleaved_identities(
    tmp_path: Path,
) -> None:
    base = tmp_path / "interleaved-reply-identities"
    base.mkdir()
    replies = {
        "9101": ("1101", "First immutable reply."),
        "9102": ("1102", "Second immutable reply."),
    }
    state = {
        "daily_reply_count": 2,
        "tweet_cache": {},
        "ai_reply_history": [],
    }
    for reply_id, (target_id, text) in replies.items():
        state["tweet_cache"][reply_id] = {
            "id": reply_id,
            "author_id": "12345",
            "conversation_id": target_id,
            "referenced_tweets": [{"type": "replied_to", "id": target_id}],
            "text": text,
            "cached_epoch": 1_788_121_200,
            "post_type": "auto_reply",
        }
        state["ai_reply_history"].append(
            {
                "target_id": target_id,
                "reply_post_id": reply_id,
                "candidate_source": "mention",
                "proposed_reply": text,
            }
        )
    write_json(base / "bot_state.json", state)
    write_digest_log(
        base,
        [
            "2026-08-30 20:20:00 INFO maybe_reply_to_mentions:200 - Considering mention id=1101 author_id=601 text='First input'",
            "2026-08-30 20:20:01 INFO maybe_reply_to_mentions:201 - Generated reply to mention 1101: 'First preview.'",
            "2026-08-30 20:20:02 INFO maybe_reply_to_mentions:202 - Recorded and cached own auto-reply id=9101",
            "2026-08-30 20:20:03 INFO maybe_reply_to_mentions:203 - Reply posted successfully",
            "2026-08-30 20:20:04 INFO maybe_reply_to_mentions:204 - Considering mention id=1102 author_id=602 text='Second input'",
            "2026-08-30 20:20:05 INFO maybe_reply_to_mentions:205 - Generated reply to mention 1102: 'Second preview.'",
            "2026-08-30 20:20:06 INFO maybe_reply_to_mentions:206 - Recorded and cached own auto-reply id=9102",
            "2026-08-30 20:20:07 INFO maybe_reply_to_mentions:207 - Reply posted successfully",
            digest_event_line(
                "2026-08-30 20:20:08",
                "reply_posted",
                lane="mention",
                target_id="1102",
                reply_post_id="9102",
            ),
            digest_event_line(
                "2026-08-30 20:20:09",
                "reply_posted",
                lane="mention",
                target_id="1101",
                reply_post_id="9101",
            ),
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    outcomes = {
        event["reply_post_id"]: event
        for event in payload["events"]
        if event.get("kind") == "mention_reply_posted"
    }
    assert set(outcomes) == set(replies)
    for reply_id, (target_id, text) in replies.items():
        assert outcomes[reply_id]["target_id"] == target_id
        assert outcomes[reply_id]["public_reply_text"] == text
        assert outcomes[reply_id]["correlation_status"] == "exact"


def test_digest_reply_text_conflict_is_explicit_and_does_not_choose_a_text(
    tmp_path: Path,
) -> None:
    base = tmp_path / "conflicting-reply-text"
    base.mkdir()
    reply_id = "9201"
    target_id = "1201"
    write_json(
        base / "bot_state.json",
        {
            "daily_reply_count": 1,
            "tweet_cache": {
                reply_id: {
                    "id": reply_id,
                    "author_id": "12345",
                    "conversation_id": target_id,
                    "referenced_tweets": [
                        {"type": "replied_to", "id": target_id}
                    ],
                    "text": "Confirmed cache evidence A.",
                    "cached_epoch": 1_788_121_800,
                    "post_type": "auto_reply",
                }
            },
            "ai_reply_history": [
                {
                    "target_id": target_id,
                    "reply_post_id": reply_id,
                    "candidate_source": "mention",
                    "proposed_reply": "Confirmed history evidence B.",
                }
            ],
        },
    )
    write_digest_log(
        base,
        [
            f"2026-08-30 20:29:55 INFO maybe_reply_to_mentions:290 - Considering mention id={target_id} author_id=701 text='Conflicting input'",
            f"2026-08-30 20:29:56 INFO maybe_reply_to_mentions:291 - Generated reply to mention {target_id}: 'Short preview.'",
            f"2026-08-30 20:29:57 INFO maybe_reply_to_mentions:292 - Recorded and cached own auto-reply id={reply_id}",
            "2026-08-30 20:29:58 INFO maybe_reply_to_mentions:293 - Reply posted successfully",
            digest_event_line(
                "2026-08-30 20:30:00",
                "reply_posted",
                lane="mention",
                target_id=target_id,
                reply_post_id=reply_id,
            )
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    event = next(
        item
        for item in payload["events"]
        if item.get("kind") == "mention_reply_posted"
    )
    assert event["reply_post_id"] == reply_id
    assert event["public_reply_text"] is None
    assert event["public_reply_text_sha256"] is None
    assert event["public_reply_text_character_count"] is None
    assert event["public_reply_text_complete"] is False
    assert event["public_reply_text_status"] == "conflict"
    assert event["correlation_status"] == "conflict"
    assert "disagree" in event["public_reply_text_reason"].lower()
    assert len(event["public_reply_text_reason"]) <= 500


def test_digest_api_health_counter_semantics_distinguish_successful_observed_transports(
    tmp_path: Path,
) -> None:
    base = tmp_path / "api-counter-semantics-success"
    transaction_id = "a" * 64
    write_digest_log(
        base,
        [
            "2026-08-30 20:40:00 INFO upload_media:200 - Uploading receipt-bound media via X API v2: /tmp/t01.jpg",
            "2026-08-30 20:40:02 INFO create_post:202 - Creating X post with durable transport journal. "
            f"lane=mention transaction_id={transaction_id} reply_to_id=1301 "
            "media_count=1 made_with_ai=True",
            "2026-08-30 20:40:03 INFO create_post:202 - Creating X post with durable transport journal. "
            f"lane=mention transaction_id={transaction_id} reply_to_id=1301 "
            "media_count=1 made_with_ai=True",
            "2026-08-30 20:40:04 INFO create_post:204 - Created X post successfully. "
            "response={'data': {'id': '9301', 'text': 'Confirmed reply.'}}",
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    health = payload["api_health"]
    assert health["posting_attempt_count"] == 0
    assert health["tweet_create_request_count"] == 0
    assert health["media_upload_request_count"] == 0
    semantics = health["counter_semantics"]
    assert "failed" in json.dumps(semantics["posting_attempt_count"]).lower()
    assert "/2/tweets" in json.dumps(
        semantics["tweet_create_request_count"]
    ).lower()
    assert "/2/media/upload" in json.dumps(
        semantics["media_upload_request_count"]
    ).lower()
    for counter_name in (
        "tweet_create_request_count",
        "media_upload_request_count",
        "observed_tweet_transport_request_count",
        "observed_media_upload_request_count",
    ):
        scope = str(semantics[counter_name]["scope"])
        assert "message" in scope.lower()
        assert "DEBUG" not in scope
        assert "INFO" not in scope
    assert health["observed_tweet_transport_request_count"] == 1
    assert health["observed_media_upload_request_count"] == 1
    assert health["observed_remote_write_success_count"] == 1
    by_lane = health["observed_tweet_transport_request_counts_by_lane"]
    assert by_lane["mention"] == 1
    assert sum(by_lane.values()) == 1


def test_digest_api_health_counter_semantics_keep_failed_requests_distinct(
    tmp_path: Path,
) -> None:
    base = tmp_path / "api-counter-semantics-failure"
    transaction_id = "b" * 64
    write_digest_log(
        base,
        [
            "2026-08-30 20:50:00 INFO create_post:300 - Creating X post with durable transport journal. "
            f"lane=quote_tweet transaction_id={transaction_id} reply_to_id=1401 "
            "media_count=0 made_with_ai=True",
            "2026-08-30 20:50:01 INFO x_request:301 - X request: POST https://api.x.com/2/tweets",
            "2026-08-30 20:50:02 ERROR x_request:302 - X API error 503: service unavailable",
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    health = payload["api_health"]
    assert health["posting_attempt_count"] == 1
    assert health["tweet_create_request_count"] == 1
    assert health["media_upload_request_count"] == 0
    assert health["observed_tweet_transport_request_count"] == 1
    assert health["observed_media_upload_request_count"] == 0
    assert health["observed_remote_write_success_count"] == 0
    assert health["observed_tweet_transport_request_counts_by_lane"][
        "quote_tweet"
    ] == 1
    assert health["errors"][0]["status"] == "503"


def test_digest_state_derived_quarantine_strike_progress_has_no_false_log_provenance(
    tmp_path: Path,
) -> None:
    base = tmp_path / "state-derived-strike-provenance"
    base.mkdir()
    current_epoch = int(time.time())
    write_json(
        base / "bot_state.json",
        {
            "daily_reply_count": 0,
            "author_evaluation_quarantines": {
                "1501": {
                    "recent_no_reply_epochs": [current_epoch - 60],
                    "quarantine_until_epoch": 0,
                    "last_updated_epoch": current_epoch - 60,
                    "latest_explicit_spam_or_abuse_epoch": current_epoch - 60,
                    "evidence_policy": (
                        "majority_resolvable_terminal_no_reply_v3"
                    ),
                }
            },
        },
    )
    write_json(
        base / "mrsMThatcher.local.json",
        {
            "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD": 3,
            "AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS": 21600,
            "AUTHOR_NO_REPLY_QUARANTINE_SECONDS": 43200,
        },
    )
    write_digest_log(
        base,
        ["2026-08-30 21:00:00 INFO     main:400 - Main loop tick"],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    progress = payload["mention_backlog_and_quarantine"][
        "current_author_no_reply_strike_progress"
    ]
    assert progress["available"] is True
    assert progress["source"] == "bot_state.json"
    assert "source_refs" not in progress
    assert progress["authors"][0]["author_id"] == "1501"
    assert "source_refs" not in progress["authors"][0]


def test_digest_structured_only_confirmation_is_normalised_and_unidentified_legacy_reply_is_explicitly_unavailable(
    tmp_path: Path,
) -> None:
    base = tmp_path / "structured-only-and-legacy-reply-text"
    base.mkdir()
    structured_target_id = "1601"
    structured_reply_id = "9601"
    exact_text = "A structured-only confirmed reply.\nWith its exact second line."
    write_json(
        base / "bot_state.json",
        {
            "daily_reply_count": 2,
            "tweet_cache": {
                structured_reply_id: {
                    "id": structured_reply_id,
                    "author_id": "12345",
                    "conversation_id": structured_target_id,
                    "referenced_tweets": [
                        {"type": "replied_to", "id": structured_target_id}
                    ],
                    "text": exact_text,
                    "cached_epoch": 1_788_124_200,
                    "post_type": "auto_reply",
                }
            },
            "ai_reply_history": [
                {
                    "target_id": structured_target_id,
                    "reply_post_id": structured_reply_id,
                    "candidate_source": "mention",
                    "reply_epoch": 1_788_124_200,
                    "proposed_reply": exact_text,
                }
            ],
        },
    )
    write_digest_log(
        base,
        [
            digest_event_line(
                "2026-08-30 21:10:00",
                "reply_posted",
                lane="mention",
                target_id=structured_target_id,
                reply_post_id=structured_reply_id,
                author_id="801",
            ),
            "2026-08-30 21:11:00 INFO maybe_reply_to_mentions:500 - "
            "Considering mention id=1602 author_id=802 text='Legacy input retained'",
            "2026-08-30 21:11:01 INFO maybe_reply_to_mentions:501 - "
            "Generated reply to mention 1602: 'Legacy preview retained.'",
            "2026-08-30 21:11:02 INFO maybe_reply_to_mentions:502 - "
            "Recorded and cached own auto-reply id=9602",
            "2026-08-30 21:11:03 INFO maybe_reply_to_mentions:503 - "
            "Reply posted successfully",
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    events = payload["events"]
    normalised = next(
        event
        for event in events
        if event.get("kind") == "confirmed_public_reply"
    )
    assert normalised["time"] == "2026-08-30 21:10:00"
    assert normalised["lane"] == "mention"
    assert normalised["target_id"] == structured_target_id
    assert normalised["reply_post_id"] == structured_reply_id
    assert normalised["public_reply_text"] == exact_text
    assert normalised["public_reply_text_sha256"] == hashlib.sha256(
        exact_text.encode("utf-8")
    ).hexdigest()
    assert normalised["public_reply_text_character_count"] == len(exact_text)
    assert normalised["public_reply_text_complete"] is True
    assert normalised["public_reply_text_status"] == "confirmed"
    assert "bot_state.json.ai_reply_history" in normalised[
        "public_reply_text_source"
    ]
    assert "bot_state.json.tweet_cache" in normalised[
        "public_reply_text_source"
    ]
    assert normalised["public_reply_text_reason"] == ""
    assert normalised["correlation_status"] == "exact"

    legacy = next(
        event
        for event in events
        if event.get("kind") == "mention_reply_posted"
    )
    assert legacy["time"] == "2026-08-30 21:11:03"
    assert events.index(normalised) < events.index(legacy)
    assert normalised["time"] < legacy["time"]
    assert legacy["mention_id"] == "1602"
    assert legacy["author_id"] == "802"
    assert legacy["incoming_text"] == "Legacy input retained"
    assert legacy["reply"] == "Legacy preview retained."
    assert legacy["reply_post_id"] == "9602"
    assert legacy["public_reply_text"] is None
    assert legacy["public_reply_text_sha256"] is None
    assert legacy["public_reply_text_character_count"] is None
    assert legacy["public_reply_text_complete"] is False
    assert legacy["public_reply_text_status"] == "unavailable"
    assert legacy["public_reply_text_source"] is None
    assert 0 < len(legacy["public_reply_text_reason"]) <= 320
    assert legacy["correlation_status"] == "unavailable"


@pytest.mark.parametrize(
    "confirmation_fields",
    [
        {"target_id": "1701", "reply_post_id": "9701"},
        {
            "lane": "not_a_reply_lane",
            "target_id": "1702",
            "reply_post_id": "9702",
        },
        {"lane": "mention", "reply_post_id": "9703"},
        {
            "lane": "mention",
            "target_id": "not-a-numeric-target",
            "reply_post_id": "9704",
        },
    ],
    ids=("missing-lane", "invalid-lane", "missing-target", "invalid-target"),
)
def test_digest_malformed_reply_confirmation_is_not_counted_as_remote_write_success(
    tmp_path: Path,
    confirmation_fields: dict[str, str],
) -> None:
    base = tmp_path / "malformed-reply-confirmation"
    write_digest_log(
        base,
        [
            digest_event_line(
                "2026-08-30 21:20:00",
                "reply_posted",
                **confirmation_fields,
            )
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["api_health"]["observed_remote_write_success_count"] == 0
    assert not any(
        event.get("kind") == "confirmed_public_reply"
        for event in payload["events"]
    )


@pytest.mark.parametrize(
    "mismatch",
    (
        "history-target",
        "history-lane",
        "cache-target",
        "cache-extra-malformed-target",
        "cache-post-type",
    ),
)
def test_digest_durable_reply_identity_mismatch_is_an_explicit_text_conflict(
    tmp_path: Path,
    mismatch: str,
) -> None:
    base = tmp_path / f"durable-reply-identity-{mismatch}"
    base.mkdir()
    target_id = "1801"
    reply_id = "9801"
    exact_text = "Exact durable reply text that must not cross identities."
    history = {
        "target_id": target_id,
        "reply_post_id": reply_id,
        "candidate_source": "mention",
        "reply_epoch": 1_788_124_800,
        "proposed_reply": exact_text,
    }
    cached = {
        "id": reply_id,
        "author_id": "12345",
        "conversation_id": target_id,
        "referenced_tweets": [{"type": "replied_to", "id": target_id}],
        "text": exact_text,
        "cached_epoch": 1_788_124_800,
        "post_type": "auto_reply",
    }
    if mismatch == "history-target":
        history["target_id"] = "1802"
    elif mismatch == "history-lane":
        history["candidate_source"] = "quote_tweet"
    elif mismatch == "cache-target":
        cached["referenced_tweets"] = [
            {"type": "replied_to", "id": "1802"}
        ]
    elif mismatch == "cache-extra-malformed-target":
        cached["referenced_tweets"] = [
            {"type": "replied_to", "id": target_id},
            {"type": "replied_to", "id": int(target_id)},
        ]
    else:
        cached["post_type"] = "quote_image"
    write_json(
        base / "bot_state.json",
        {
            "daily_reply_count": 1,
            "ai_reply_history": [history],
            "tweet_cache": {reply_id: cached},
        },
    )
    write_digest_log(
        base,
        [
            digest_event_line(
                "2026-08-30 21:30:00",
                "reply_posted",
                lane="mention",
                target_id=target_id,
                reply_post_id=reply_id,
                author_id="901",
            )
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    event = next(
        item
        for item in payload["events"]
        if item.get("kind") == "confirmed_public_reply"
    )
    assert event["target_id"] == target_id
    assert event["reply_post_id"] == reply_id
    assert event["public_reply_text"] is None
    assert event["public_reply_text_sha256"] is None
    assert event["public_reply_text_character_count"] is None
    assert event["public_reply_text_complete"] is False
    assert event["public_reply_text_status"] == "conflict"
    assert event["correlation_status"] == "conflict"
    assert 0 < len(event["public_reply_text_reason"]) <= 320


def test_digest_quote_reply_original_post_identity_disagreement_conflicts(
    tmp_path: Path,
) -> None:
    base = tmp_path / "quote-reply-original-post-conflict"
    base.mkdir()
    target_id = "1811"
    reply_id = "9811"
    exact_text = "Exact quote-tweet reply text."
    write_json(
        base / "bot_state.json",
        {
            "daily_reply_count": 1,
            "ai_reply_history": [
                {
                    "target_id": target_id,
                    "reply_post_id": reply_id,
                    "candidate_source": "quote_tweet",
                    "proposed_reply": exact_text,
                }
            ],
            "tweet_cache": {
                reply_id: {
                    "id": reply_id,
                    "referenced_tweets": [
                        {"type": "replied_to", "id": target_id}
                    ],
                    "text": exact_text,
                    "post_type": "auto_reply",
                }
            },
        },
    )
    write_digest_log(
        base,
        [
            "2026-08-30 21:31:00 INFO maybe_reply_to_quote_tweets:600 - "
            "Considering quote tweet id=1811 author_id=902 "
            "original_post_id=2811 text='Quote input'",
            "2026-08-30 21:31:01 INFO maybe_reply_to_quote_tweets:601 - "
            "Generated reply to quote tweet 1811: 'Quote preview.'",
            "2026-08-30 21:31:02 INFO maybe_reply_to_quote_tweets:602 - "
            "Recorded and cached own quote-tweet auto-reply id=9811",
            "2026-08-30 21:31:03 INFO maybe_reply_to_quote_tweets:603 - "
            "Quote-tweet reply posted successfully",
            digest_event_line(
                "2026-08-30 21:31:04",
                "reply_posted",
                lane="quote_tweet",
                target_id=target_id,
                reply_post_id=reply_id,
                original_post_id="2812",
                author_id="902",
            ),
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    event = next(
        item
        for item in payload["events"]
        if item.get("kind") == "quote_tweet_reply_posted"
    )
    assert event["quote_tweet_id"] == target_id
    assert event["reply_post_id"] == reply_id
    assert event["original_post_id"] == "2811"
    assert event["public_reply_text"] is None
    assert event["public_reply_text_sha256"] is None
    assert event["public_reply_text_character_count"] is None
    assert event["public_reply_text_complete"] is False
    assert event["public_reply_text_status"] == "conflict"
    assert event["correlation_status"] == "conflict"
    assert "identity" in event["public_reply_text_reason"]


@pytest.mark.parametrize(
    "invalid_field",
    ("boolean-version", "root-post-id", "conversation-id"),
)
def test_digest_historical_publication_rejects_invalid_authority_identity(
    tmp_path: Path,
    invalid_field: str,
) -> None:
    base = tmp_path / f"invalid-historical-authority-{invalid_field}"
    parent_id = "1821"
    reply_id = "9821"
    fields: dict[str, object] = {
        "event_version": 1,
        "lane": "historical_context_reply",
        "parent_post_id": parent_id,
        "reply_post_id": reply_id,
        "root_post_id": parent_id,
        "conversation_id": parent_id,
        "reply_text": "Text without valid publication authority.",
        "quote_id": "b" * 64,
        "publication_authority": "confirmed_transport",
    }
    if invalid_field == "boolean-version":
        fields["event_version"] = True
    elif invalid_field == "root-post-id":
        fields["root_post_id"] = "1822"
    else:
        fields["conversation_id"] = "1822"
    write_digest_log(
        base,
        [
            digest_event_line(
                "2026-08-30 21:32:00",
                "historical_context_reply_posted",
                **fields,
            )
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert not any(
        event.get("kind") == "confirmed_public_reply"
        and event.get("reply_post_id") == reply_id
        for event in payload["events"]
    )
    assert payload["api_health"]["observed_remote_write_success_count"] == 0


def test_digest_structured_only_historical_confirmation_normalises_exact_text(
    tmp_path: Path,
) -> None:
    base = tmp_path / "structured-only-historical-confirmation"
    parent_id = "1831"
    reply_id = "9831"
    quote_id = "c" * 64
    exact_text = "Historical exact first line.\n\nHistorical exact final line."
    write_digest_log(
        base,
        [
            digest_event_line(
                "2026-08-30 21:33:00",
                "historical_context_reply_posted",
                event_version=1,
                lane="historical_context_reply",
                parent_post_id=parent_id,
                reply_post_id=reply_id,
                root_post_id=parent_id,
                conversation_id=parent_id,
                reply_text=exact_text,
                quote_id=quote_id,
                publication_authority="confirmed_transport",
            )
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    event = next(
        item
        for item in payload["events"]
        if item.get("kind") == "confirmed_public_reply"
    )
    assert event is payload["events"][-1]
    assert event["lane"] == "historical_context_reply"
    assert event["target_id"] == parent_id
    assert event["parent_post_id"] == parent_id
    assert event["reply_post_id"] == reply_id
    assert event["quote_id"] == quote_id
    assert event["public_reply_text"] == exact_text
    assert event["public_reply_text_sha256"] == hashlib.sha256(
        exact_text.encode("utf-8")
    ).hexdigest()
    assert event["public_reply_text_character_count"] == len(exact_text)
    assert event["public_reply_text_complete"] is True
    assert event["public_reply_text_status"] == "confirmed"
    assert "historical_context_reply_posted" in event[
        "public_reply_text_source"
    ]
    assert event["public_reply_text_reason"] == ""
    assert event["correlation_status"] == "exact"
    assert payload["api_health"]["observed_remote_write_success_count"] == 1


def test_digest_structured_main_post_success_deduplicates_generic_success(
    tmp_path: Path,
) -> None:
    post_id = "9841"
    observed_counts: list[int] = []
    for include_generic_success in (False, True):
        base = tmp_path / (
            "structured-main-post-with-generic"
            if include_generic_success
            else "structured-main-post-only"
        )
        lines = [
            digest_event_line(
                "2026-08-30 21:34:00",
                "main_post_posted",
                lane="quote_image",
                post_id=post_id,
                line_no=14,
                image_no=3,
                image_basename="t04.jpg",
                quote_hash="d" * 64,
            )
        ]
        if include_generic_success:
            lines.append(
                "2026-08-30 21:34:01 INFO create_post:700 - "
                "Created X post successfully. "
                f"response={{'data': {{'id': '{post_id}'}}}}"
            )
        write_digest_log(base, lines)
        result = run_digest(base, as_json=True)
        assert result.returncode == 0, result.stderr
        observed_counts.append(
            json.loads(result.stdout)["api_health"][
                "observed_remote_write_success_count"
            ]
        )

    assert observed_counts == [1, 1]


def test_digest_genuinely_interleaved_legacy_reply_cannot_suppress_exact_identity(
    tmp_path: Path,
) -> None:
    """Immutable confirmation wins when mutable legacy pending state crosses wires."""

    base = tmp_path / "genuinely-interleaved-reply-identities"
    base.mkdir()
    confirmed_target_id = "1901"
    unrelated_pending_target_id = "1902"
    reply_id = "9901"
    exact_text = "Immutable evidence retains this exact confirmed reply."
    write_json(
        base / "bot_state.json",
        {
            "daily_reply_count": 1,
            "ai_reply_history": [
                {
                    "target_id": confirmed_target_id,
                    "reply_post_id": reply_id,
                    "candidate_source": "mention",
                    "proposed_reply": exact_text,
                }
            ],
            "tweet_cache": {
                reply_id: {
                    "id": reply_id,
                    "post_type": "auto_reply",
                    "text": exact_text,
                    "referenced_tweets": [
                        {
                            "type": "replied_to",
                            "id": confirmed_target_id,
                        }
                    ],
                }
            },
        },
    )
    write_digest_log(
        base,
        [
            "2026-08-30 21:40:00 INFO maybe_reply_to_mentions:800 - "
            f"Considering mention id={confirmed_target_id} author_id=1001 "
            "text='First input'",
            "2026-08-30 21:40:01 INFO maybe_reply_to_mentions:801 - "
            f"Generated reply to mention {confirmed_target_id}: 'First preview.'",
            # A second attempt replaces the parser's mutable legacy pending
            # record before the first immutable reply identity is logged.
            "2026-08-30 21:40:02 INFO maybe_reply_to_mentions:802 - "
            f"Considering mention id={unrelated_pending_target_id} "
            "author_id=1002 text='Second input'",
            "2026-08-30 21:40:03 INFO maybe_reply_to_mentions:803 - "
            f"Recorded and cached own auto-reply id={reply_id}",
            "2026-08-30 21:40:04 INFO maybe_reply_to_mentions:804 - "
            "Reply posted successfully",
            digest_event_line(
                "2026-08-30 21:40:05",
                "reply_posted",
                lane="mention",
                target_id=confirmed_target_id,
                reply_post_id=reply_id,
                author_id="1001",
            ),
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    exact = [
        event
        for event in payload["events"]
        if event.get("reply_post_id") == reply_id
        and event.get("public_reply_text_status") == "confirmed"
    ]
    assert len(exact) == 1
    assert exact[0]["kind"] == "confirmed_public_reply"
    assert exact[0]["lane"] == "mention"
    assert exact[0]["target_id"] == confirmed_target_id
    assert exact[0]["public_reply_text"] == exact_text
    assert exact[0]["correlation_status"] == "exact"

    crossed_legacy = next(
        event
        for event in payload["events"]
        if event.get("kind") == "mention_reply_posted"
    )
    assert crossed_legacy["mention_id"] == unrelated_pending_target_id
    assert crossed_legacy["reply_post_id"] == reply_id
    assert crossed_legacy["public_reply_text"] is None
    assert crossed_legacy["public_reply_text_status"] == "conflict"
    assert crossed_legacy["correlation_status"] == "conflict"


@pytest.mark.parametrize(
    ("receipt_schema_version", "exact_text", "expected_confirmed"),
    [
        pytest.param(
            2,
            "The confirmed receipt is durable authority and remains single-line.",
            True,
            id="v2-valid",
        ),
        pytest.param(
            3,
            "The confirmed receipt is durable authority and remains single-line.",
            True,
            id="v3-valid",
        ),
        pytest.param(
            4,
            "The confirmed receipt is durable authority and remains single-line.",
            True,
            id="v4-valid",
        ),
        pytest.param(3, "a" * 271, True, id="long-post"),
        pytest.param(3, "界" * 136, True, id="weighted-long-post"),
        pytest.param(3, "First line\nSecond line", True, id="line-break"),
    ],
)
def test_digest_uses_validated_confirmed_conversational_receipt_text_fallback(
    tmp_path: Path,
    receipt_schema_version: int,
    exact_text: str,
    expected_confirmed: bool,
) -> None:
    base = tmp_path / (
        f"confirmed-conversational-receipt-v{receipt_schema_version}-fallback"
    )
    base.mkdir()
    target_id = "1911"
    reply_id = "9911"
    contribution = "A civil contribution awaiting durable reconciliation."
    reply_context = {
        "lane": "mention",
        "target_id": target_id,
        "thread_id": target_id,
        "incoming_contribution": contribution,
        "quoted_post": None,
        "parent_thread": [],
        "clarification_request": None,
        "current_date": "2026-08-30",
    }
    value_hash = lambda value: hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    draft = {
        "schema_version": 1,
        "strategy_version": "tested-reply-pipeline-20260817",
        "target_id": target_id,
        "thread_id": target_id,
        "candidate_source": "mention",
        "contribution_hash": hashlib.sha256(
            contribution.encode("utf-8")
        ).hexdigest(),
        "context_hash": value_hash(reply_context),
        "trusted_facts_hash": value_hash([]),
        "proposed_reply": exact_text,
        "mode": "opinion_or_principle",
        "final_reply_kind": "unknown",
        "tone": "unknown",
        "factual_claims": [],
        "evidence_ids": None,
        "trusted_facts_supplied_count": 0,
        "trusted_fact_ids_supplied": [],
        "used_fact_count": "unknown",
        "used_fact_ids": None,
        "claim_risk_categories": [],
        "reviewer_verdict": "approve",
        "model_call_count": 3,
        "revision_count": 0,
        "reply_requirement": "general",
        "route_source": "xai_gate",
        "creation_time": "2026-08-30T20:40:00Z",
    }
    draft["approval_hash"] = value_hash(draft)
    receipt = {
        "schema_version": receipt_schema_version,
        "lifecycle_state": "confirmed",
        "target_id": target_id,
        "reply_post_id": reply_id,
        "author_id": "1011",
        "reply_epoch": 1_788_125_000,
        "daily_reply_date": "2026-08-30",
        "candidate_source": "mention",
        "conversation_id": target_id,
        "reply_text": exact_text,
        "reply_context": reply_context,
        "ai_reply_draft": draft,
    }
    if receipt_schema_version == 2:
        receipt.pop("lifecycle_state")
    elif receipt_schema_version == 4:
        attempt_epoch = 1_788_124_900
        receipt.update(
            {
                "attempt_epoch": attempt_epoch,
                "confirmation_epoch": receipt["reply_epoch"],
            }
        )
        sending_receipt = dict(receipt)
        sending_receipt.pop("reply_post_id")
        sending_receipt.pop("confirmation_epoch")
        sending_receipt["lifecycle_state"] = "sending"
        sending_receipt["reply_epoch"] = attempt_epoch
        receipt["source_receipt_sha256"] = hashlib.sha256(
            (
                json.dumps(sending_receipt, indent=2, sort_keys=True)
                + "\n"
            ).encode("utf-8")
        ).hexdigest()
    receipt_path = base / "confirmed_reply_receipt.json"
    receipt_path.write_bytes(
        (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )
    receipt_path.chmod(0o600)
    write_digest_log(
        base,
        [
            "2026-08-30 21:41:00 INFO scheduler:1 - Durable reply reconciliation pending"
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    if not expected_confirmed:
        assert not any(
            item.get("kind") == "confirmed_public_reply"
            for item in payload["events"]
        )
        receipt_health = payload["published_reply_text_health"][
            "durable_evidence"
        ]["confirmed_reply_receipt"]
        assert receipt_health["available"] is False
        assert receipt_health["status"] == "unavailable"
        return
    event = next(
        item
        for item in payload["events"]
        if item.get("kind") == "confirmed_public_reply"
    )
    assert event["target_id"] == target_id
    assert event["reply_post_id"] == reply_id
    assert event["public_reply_text"] == exact_text
    assert event["public_reply_text_sha256"] == hashlib.sha256(
        exact_text.encode("utf-8")
    ).hexdigest()
    assert event["public_reply_text_character_count"] == len(exact_text)
    assert event["public_reply_text_complete"] is True
    assert event["public_reply_text_status"] == "confirmed"
    assert "confirmed_reply_receipt.json" in event[
        "public_reply_text_source"
    ]
    assert event["correlation_status"] == "exact"
    assert event["publication_authority"] == "confirmed_reply_receipt.json"
    assert "independent of the selected log window" in event["evidence_scope"]


def test_digest_rejects_production_invalid_confirmed_receipt_shape(
    tmp_path: Path,
) -> None:
    base = tmp_path / "invalid-confirmed-receipt-shape"
    base.mkdir()
    invalid_text = "A malformed receipt must not establish publication."
    write_private_json(
        base / "confirmed_reply_receipt.json",
        {
            "schema_version": 3,
            "lifecycle_state": "confirmed",
            "target_id": 1912,
            "reply_post_id": 9912,
            "author_id": 1012,
            "reply_epoch": 1_788_125_001,
            "daily_reply_date": ["not", "a", "date"],
            "candidate_source": "mention_reply",
            "conversation_id": 1912,
            "reply_text": invalid_text,
            "reply_context": {
                "lane": "mention",
                "target_id": 1912,
                "thread_id": 1912,
            },
            "ai_reply_draft": {"proposed_reply": invalid_text},
        },
    )
    write_digest_log(
        base,
        ["2026-08-30 21:41:30 INFO scheduler:1 - No confirmed reply event"],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    status = payload["published_reply_text_health"]["durable_evidence"][
        "confirmed_reply_receipt"
    ]
    assert status["status"] == "unavailable"
    assert not any(
        item.get("kind") == "confirmed_public_reply"
        for item in payload["events"]
    )
    assert invalid_text not in result.stdout


def test_digest_uses_completed_historical_history_outside_selected_log_evidence(
    tmp_path: Path,
) -> None:
    base = tmp_path / "completed-historical-history-fallback"
    base.mkdir()
    parent_id = "1921"
    reply_id = "9921"
    quote_id = "e" * 64
    preview = "Context — retained preview…"
    exact_text = "Context — durable completed history.\n\nMeaning — exact text retained."
    write_private_json(
        base / "historical_context_reply_history.json",
        {
            "schema_version": 1,
            "items": {
                parent_id: {
                    "schema_version": 1,
                    "parent_post_id": parent_id,
                    "reply_post_id": reply_id,
                    "quote_id": quote_id,
                    "reply_text": exact_text,
                    "reply_epoch": 1_788_125_100,
                    "confirmed_at": "2026-08-30T20:41:40Z",
                    "status": "completed",
                }
            },
        },
    )
    write_digest_log(
        base,
        [
            digest_event_line(
                "2026-08-30 21:42:00",
                "historical_context_reply",
                status="already_completed",
                parent_post_id=parent_id,
                quote_id=quote_id,
                character_count=len(exact_text),
                reply_preview=preview,
            )
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    event = next(
        item
        for item in payload["events"]
        if item.get("kind") == "historical_context_reply"
    )
    assert event["reply_preview"] == preview
    assert event["parent_post_id"] == parent_id
    assert event["reply_post_id"] == reply_id
    assert event["public_reply_text"] == exact_text
    assert event["public_reply_text_sha256"] == hashlib.sha256(
        exact_text.encode("utf-8")
    ).hexdigest()
    assert event["public_reply_text_character_count"] == len(exact_text)
    assert event["public_reply_text_complete"] is True
    assert event["public_reply_text_status"] == "confirmed"
    assert "historical_context_reply_history.json" in event[
        "public_reply_text_source"
    ]
    assert event["correlation_status"] == "exact"


def test_digest_ignores_nonauthoritative_historical_posted_text_when_history_is_valid(
    tmp_path: Path,
) -> None:
    base = tmp_path / "nonauthoritative-historical-posted-evidence"
    base.mkdir()
    parent_id = "1922"
    reply_id = "9922"
    quote_id = "f" * 64
    exact_text = "Context — durable history wins.\n\nMeaning — exact confirmed text."
    untrusted_text = "A structurally incomplete event must not poison durable text."
    write_private_json(
        base / "historical_context_reply_history.json",
        {
            "schema_version": 1,
            "items": {
                parent_id: {
                    "schema_version": 1,
                    "parent_post_id": parent_id,
                    "reply_post_id": reply_id,
                    "quote_id": quote_id,
                    "reply_text": exact_text,
                    "reply_epoch": 1_788_125_150,
                    "confirmed_at": "2026-08-30T20:42:30Z",
                    "status": "completed",
                }
            },
        },
    )
    write_digest_log(
        base,
        [
            # The immutable authority fields deliberately omit event_version,
            # so this is only an observed lookalike, not publication proof.
            digest_event_line(
                "2026-08-30 21:42:20",
                "historical_context_reply_posted",
                lane="historical_context_reply",
                parent_post_id=parent_id,
                reply_post_id=reply_id,
                root_post_id=parent_id,
                conversation_id=parent_id,
                reply_text=untrusted_text,
                quote_id=quote_id,
                publication_authority="confirmed_transport",
            ),
            digest_event_line(
                "2026-08-30 21:42:30",
                "historical_context_reply",
                status="already_completed",
                parent_post_id=parent_id,
                quote_id=quote_id,
                character_count=len(exact_text),
                reply_preview="Context — durable history…",
            ),
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    event = next(
        item
        for item in payload["events"]
        if item.get("kind") == "historical_context_reply"
    )
    assert event["reply_post_id"] == reply_id
    assert event["public_reply_text"] == exact_text
    assert event["public_reply_text_status"] == "confirmed"
    assert event["correlation_status"] == "exact"
    assert event["public_reply_text_source"] == (
        "historical_context_reply_history.json"
    )


@pytest.mark.parametrize("integer_field", ("parent_post_id", "quote_id"))
def test_digest_does_not_coerce_selected_historical_identity_for_correlation(
    tmp_path: Path,
    integer_field: str,
) -> None:
    base = tmp_path / f"integer-selected-historical-{integer_field}"
    base.mkdir()
    parent_id = "1926"
    reply_id = "9926"
    quote_id = "1" * 64
    exact_text = "Durable history cannot validate a differently typed selector."
    write_private_json(
        base / "historical_context_reply_history.json",
        {
            "schema_version": 1,
            "items": {
                parent_id: {
                    "schema_version": 1,
                    "parent_post_id": parent_id,
                    "reply_post_id": reply_id,
                    "quote_id": quote_id,
                    "reply_text": exact_text,
                    "reply_epoch": 1_788_125_175,
                    "confirmed_at": "2026-08-30T20:42:55Z",
                    "status": "completed",
                }
            },
        },
    )
    selected_identity: dict[str, object] = {
        "parent_post_id": parent_id,
        "quote_id": quote_id,
    }
    selected_identity[integer_field] = int(str(selected_identity[integer_field]))
    write_digest_log(
        base,
        [
            digest_event_line(
                "2026-08-30 21:42:55",
                "historical_context_reply",
                status="already_completed",
                character_count=len(exact_text),
                reply_preview="Differently typed selector…",
                **selected_identity,
            )
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    history_status = payload["published_reply_text_health"]["durable_evidence"][
        "historical_context_reply_history"
    ]
    assert history_status["status"] == "available"
    event = next(
        item
        for item in payload["events"]
        if item.get("kind") == "historical_context_reply"
    )
    assert event["reply_post_id"] is None
    assert event["public_reply_text"] is None
    assert event["public_reply_text_status"] == "unavailable"
    assert "not canonical" in event["public_reply_text_reason"]


def test_digest_private_json_failures_do_not_echo_sensitive_object_names(
    tmp_path: Path,
) -> None:
    import mrs_log_digest as digest_module

    base = tmp_path / "private-json-reason-redaction"
    base.mkdir()
    sensitive_name = "sk-live-sensitive-marker-abcdef"
    duplicate = (
        '{"schema_version":3,"'
        + sensitive_name
        + '":1,"'
        + sensitive_name
        + '":2}\n'
    )
    for basename in (
        "confirmed_reply_receipt.json",
        "bot_state.json",
        "mrsMThatcher.local.json",
    ):
        path = base / basename
        path.write_text(duplicate, encoding="utf-8")
        path.chmod(0o600)
    write_digest_log(
        base,
        [
            "2026-08-30 21:42:30 INFO scheduler:1 - No publication in this window"
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    assert sensitive_name not in result.stdout
    assert sensitive_name not in result.stderr
    payload = json.loads(result.stdout)
    assert payload["runtime_state_status"]["status"].startswith("malformed:")
    assert payload["runtime_config_status"]["status"].startswith("malformed:")
    receipt_status = payload["published_reply_text_health"]["durable_evidence"][
        "confirmed_reply_receipt"
    ]
    assert receipt_status["status"] == "unavailable"
    assert receipt_status["reason"] == "unavailable: ValueError"
    safe_error = "archive path is not a direct reconciliation-archive child"
    assert digest_module.reconciliation_inspection_error(ValueError(safe_error)) == (
        f"ValueError: {safe_error}"
    )
    assert digest_module.reconciliation_inspection_error(
        ValueError(sensitive_name)
    ) == "inspection failed: ValueError"


def test_digest_rejects_completed_history_when_failed_sibling_is_malformed(
    tmp_path: Path,
) -> None:
    base = tmp_path / "malformed-historical-history-sibling"
    base.mkdir()
    parent_id = "1923"
    reply_id = "9923"
    quote_id = "c" * 64
    exact_text = "Completed text must not escape an invalid history authority."
    write_private_json(
        base / "historical_context_reply_history.json",
        {
            "schema_version": 1,
            "items": {
                parent_id: {
                    "schema_version": 1,
                    "parent_post_id": parent_id,
                    "reply_post_id": reply_id,
                    "quote_id": quote_id,
                    "reply_text": exact_text,
                    "reply_epoch": 1_788_125_200,
                    "confirmed_at": "2026-08-30T20:42:00Z",
                    "status": "completed",
                },
                "1924": {
                    "parent_post_id": "1924",
                    "quote_id": "d" * 64,
                    "reply_text": "This is a proved failed attempt, not a publication.",
                    "status": "failed",
                    "failure": "proved non-success",
                    "attempt_count": 1,
                    "updated_at": "not-a-canonical-utc-timestamp",
                },
            },
        },
    )
    write_digest_log(
        base,
        [
            digest_event_line(
                "2026-08-30 21:43:00",
                "historical_context_reply",
                status="already_completed",
                parent_post_id=parent_id,
                quote_id=quote_id,
                character_count=len(exact_text),
                reply_preview="Completed text preview…",
            )
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    history_status = payload["published_reply_text_health"]["durable_evidence"][
        "historical_context_reply_history"
    ]
    assert history_status["status"] == "unavailable"
    event = next(
        item
        for item in payload["events"]
        if item.get("kind") == "historical_context_reply"
    )
    assert event["reply_post_id"] is None
    assert event["public_reply_text"] is None
    assert event["public_reply_text_status"] == "unavailable"


@pytest.mark.parametrize(
    "invalid_history_kind",
    ["integer-identities", "lineage-epoch-out-of-range"],
)
def test_digest_rejects_invalid_completed_historical_history_authority(
    tmp_path: Path,
    invalid_history_kind: str,
) -> None:
    base = tmp_path / f"invalid-completed-history-{invalid_history_kind}"
    base.mkdir()
    parent_id = "1925"
    reply_id = "9925"
    quote_id = "a" * 64
    exact_text = "Invalid history must not become confirmed public evidence."
    if invalid_history_kind == "integer-identities":
        completed = {
            "schema_version": 1,
            "parent_post_id": int(parent_id),
            "reply_post_id": int(reply_id),
            "quote_id": quote_id,
            "reply_text": exact_text,
            "reply_epoch": 1_788_125_250,
            "confirmed_at": "2026-08-30T20:44:10Z",
            "status": "completed",
        }
    else:
        sending = {
            "schema_version": 1,
            "lifecycle_state": "sending",
            "parent_post_id": parent_id,
            "quote_id": quote_id,
            "reply_text": exact_text,
            "reply_epoch": 0,
            "started_at": "2026-08-30T20:44:00Z",
            "attempt_number": 1,
        }
        completed = {
            **sending,
            "lifecycle_state": "confirmed",
            "reply_post_id": reply_id,
            "confirmed_at": "2026-08-30T20:44:10Z",
            "source_receipt_sha256": hashlib.sha256(
                (
                    json.dumps(
                        sending,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                        allow_nan=False,
                    )
                    + "\n"
                ).encode("utf-8")
            ).hexdigest(),
            "status": "completed",
        }
    write_private_json(
        base / "historical_context_reply_history.json",
        {"schema_version": 1, "items": {parent_id: completed}},
    )
    write_digest_log(
        base,
        [
            digest_event_line(
                "2026-08-30 21:44:10",
                "historical_context_reply",
                status="already_completed",
                parent_post_id=parent_id,
                quote_id=quote_id,
                character_count=len(exact_text),
                reply_preview="Invalid history preview…",
            )
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    history_status = payload["published_reply_text_health"]["durable_evidence"][
        "historical_context_reply_history"
    ]
    assert history_status["status"] == "unavailable"
    event = next(
        item
        for item in payload["events"]
        if item.get("kind") == "historical_context_reply"
    )
    assert event["reply_post_id"] is None
    assert event["public_reply_text"] is None
    assert event["public_reply_text_status"] == "unavailable"


@pytest.mark.parametrize(
    "confirmation_fields",
    [
        {
            "lane": "mention",
            "target_id": "１９３１",
            "reply_post_id": "9931",
        },
        {
            "lane": "mention",
            "target_id": "1931",
            "reply_post_id": "９９３１",
        },
        {
            "lane": "mention",
            "target_id": "1" * 31,
            "reply_post_id": "9932",
        },
        {
            "lane": "mention",
            "target_id": "1932",
            "reply_post_id": "9" * 31,
        },
        {
            "lane": "quote_tweet",
            "target_id": "1933",
            "reply_post_id": "9933",
            "original_post_id": "２９３３",
        },
        {
            "lane": "mention",
            "target_id": 1934,
            "reply_post_id": "9934",
        },
        {
            "lane": "mention",
            "target_id": "1935",
            "reply_post_id": 9935,
        },
        {
            "lane": "quote_tweet",
            "target_id": "1936",
            "reply_post_id": "9936",
            "original_post_id": 2936,
        },
        {
            "lane": "mention_reply",
            "target_id": "1937",
            "reply_post_id": "9937",
        },
        {
            "lane": [],
            "target_id": "1938",
            "reply_post_id": "9938",
        },
        {
            "lane": "mention",
            "target_id": "1939",
            "reply_post_id": "9939",
            "original_post_id": [],
        },
    ],
    ids=(
        "unicode-target",
        "unicode-reply",
        "overlong-target",
        "overlong-reply",
        "unicode-quote-original",
        "integer-target",
        "integer-reply",
        "integer-quote-original",
        "alias-lane",
        "unhashable-lane",
        "unhashable-nonquote-original",
    ),
)
def test_digest_rejects_noncanonical_structured_reply_identity(
    tmp_path: Path,
    confirmation_fields: dict[str, object],
) -> None:
    base = tmp_path / "invalid-structured-reply-id"
    write_digest_log(
        base,
        [
            digest_event_line(
                "2026-08-30 21:43:00",
                "reply_posted",
                **confirmation_fields,
            )
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert not any(
        event.get("kind") == "confirmed_public_reply"
        for event in payload["events"]
    )
    assert payload["api_health"]["observed_remote_write_success_count"] == 0


@pytest.mark.parametrize(
    "integer_field",
    (
        "parent_post_id",
        "reply_post_id",
        "root_post_id",
        "conversation_id",
        "quote_id",
    ),
)
def test_digest_rejects_integer_historical_publication_identity(
    tmp_path: Path,
    integer_field: str,
) -> None:
    base = tmp_path / f"integer-historical-publication-{integer_field}"
    parent_id = "1941"
    reply_id = "9941"
    quote_id = "1" * 64
    fields: dict[str, object] = {
        "event_version": 1,
        "lane": "historical_context_reply",
        "parent_post_id": parent_id,
        "reply_post_id": reply_id,
        "root_post_id": parent_id,
        "conversation_id": parent_id,
        "reply_text": "Malformed typed identity is not publication authority.",
        "quote_id": quote_id,
        "publication_authority": "confirmed_transport",
    }
    fields[integer_field] = int(str(fields[integer_field]))
    write_digest_log(
        base,
        [
            digest_event_line(
                "2026-08-30 21:43:30",
                "historical_context_reply_posted",
                **fields,
            )
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert not any(
        event.get("kind") == "confirmed_public_reply"
        for event in payload["events"]
    )
    assert payload["api_health"]["observed_remote_write_success_count"] == 0


def test_digest_explicit_selftest_log_cannot_supply_publication_authority(
    tmp_path: Path,
) -> None:
    base = tmp_path / "explicit-selftest-publication-authority"
    base.mkdir()
    target_id = "1951"
    reply_id = "9951"
    exact_text = "State text must still require production confirmation."
    write_json(
        base / "bot_state.json",
        {
            "daily_reply_count": 0,
            "ai_reply_history": [
                {
                    "target_id": target_id,
                    "reply_post_id": reply_id,
                    "candidate_source": "mention",
                    "proposed_reply": exact_text,
                }
            ],
            "tweet_cache": {
                reply_id: {
                    "id": reply_id,
                    "post_type": "auto_reply",
                    "text": exact_text,
                    "referenced_tweets": [
                        {"type": "replied_to", "id": target_id}
                    ],
                }
            },
        },
    )
    selftest_log = base / "mrsMThatcher.selftest.log"
    selftest_log.write_text(
        "\n".join(
            [
                digest_event_line(
                    "2026-08-30 21:43:40",
                    "reply_posted",
                    lane="mention",
                    target_id=target_id,
                    reply_post_id=reply_id,
                ),
                digest_event_line(
                    "2026-08-30 21:43:41",
                    "historical_context_reply_posted",
                    event_version=1,
                    lane="historical_context_reply",
                    parent_post_id="1952",
                    reply_post_id="9952",
                    root_post_id="1952",
                    conversation_id="1952",
                    reply_text="Self-test historical text is not public evidence.",
                    quote_id="2" * 64,
                    publication_authority="confirmed_transport",
                ),
                digest_event_line(
                    "2026-08-30 21:43:42",
                    "main_post_posted",
                    lane="quote_image",
                    post_id="9953",
                ),
                digest_event_line(
                    "2026-08-30 21:43:43",
                    "account_root_posted",
                    event_version=1,
                    lane="quote_image",
                    post_id="9953",
                    root_post_id="9953",
                    conversation_id="9953",
                    publication_authority="confirmed_transport",
                ),
                "2026-08-30 21:43:44 INFO create_post:999 - "
                "Created X post successfully. "
                "response={'data': {'id': '9954'}}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(DIGEST),
            "--project-dir",
            str(base),
            "--no-state",
            "--json",
            str(selftest_log),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert not any(
        event.get("kind") == "confirmed_public_reply"
        for event in payload["events"]
    )
    assert not any(
        event.get("public_reply_text_status") == "confirmed"
        for event in payload["events"]
    )
    assert payload["published_reply_text_health"]["confirmed_record_count"] == 0
    assert payload["published_reply_text_health"]["complete_text_record_count"] == 0
    assert payload["api_health"]["observed_remote_write_success_count"] == 0


@pytest.mark.parametrize(
    "publication_event",
    (
        {
            "event": "main_post_posted",
            "lane": "quote_image",
            "post_id": 9961,
        },
        {
            "event": "main_post_posted",
            "lane": [],
            "post_id": "9962",
        },
        {
            "event": "account_root_posted",
            "event_version": 1,
            "lane": "quote_image",
            "post_id": 9963,
            "root_post_id": 9963,
            "conversation_id": 9963,
            "publication_authority": "confirmed_transport",
        },
        {
            "event": "account_root_posted",
            "event_version": 1,
            "lane": [],
            "post_id": "9964",
            "root_post_id": "9964",
            "conversation_id": "9964",
            "publication_authority": "confirmed_transport",
        },
        {
            "event": "account_root_posted",
            "event_version": 1,
            "lane": "quote_image",
            "post_id": "9965",
            "root_post_id": 9965,
            "conversation_id": "9965",
            "publication_authority": "confirmed_transport",
        },
        {
            "event": "account_root_posted",
            "event_version": 1,
            "lane": "quote_image",
            "post_id": "9966",
            "root_post_id": "9966",
            "conversation_id": 9966,
            "publication_authority": "confirmed_transport",
        },
    ),
    ids=(
        "main-integer-post",
        "main-unhashable-lane",
        "root-integer-identities",
        "root-unhashable-lane",
        "root-integer-root",
        "root-integer-conversation",
    ),
)
def test_digest_malformed_structured_main_publication_is_not_observed_success(
    tmp_path: Path,
    publication_event: dict[str, object],
) -> None:
    base = tmp_path / "malformed-structured-main-publication"
    event_name = str(publication_event["event"])
    fields = {
        key: value
        for key, value in publication_event.items()
        if key != "event"
    }
    write_digest_log(
        base,
        [
            digest_event_line(
                "2026-08-30 21:43:50",
                event_name,
                **fields,
            )
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["api_health"]["observed_remote_write_success_count"] == 0


@pytest.mark.parametrize(
    "event_fragment",
    (
        (
            '{"event":"main_post_posted","lane":"quote_image",'
            '"post_id":"9971","post_id":"9972"}'
        ),
        (
            '{"event":"reply_posted","lane":"mention",'
            '"target_id":"1972","reply_post_id":"9973",'
            '"reply_post_id":"9974"}'
        ),
        (
            '{"event":"historical_context_reply_posted",'
            '"event_version":1,"lane":"historical_context_reply",'
            '"parent_post_id":"1975","reply_post_id":"9975",'
            '"reply_post_id":"9976","root_post_id":"1975",'
            '"conversation_id":"1975","reply_text":"Exact text",'
            f'"quote_id":"{"4" * 64}",'
            '"publication_authority":"confirmed_transport"}'
        ),
        (
            'junk {"event":"main_post_posted","lane":"quote_image",'
            '"post_id":"9977"}'
        ),
        (
            'prefix=garbage {"event":"reply_posted","lane":"mention",'
            '"target_id":"1978","reply_post_id":"9978"}'
        ),
        (
            '\t{"event":"historical_context_reply_posted",'
            '"event_version":1,"lane":"historical_context_reply",'
            '"parent_post_id":"1979","reply_post_id":"9979",'
            '"root_post_id":"1979","conversation_id":"1979",'
            '"reply_text":"Exact text",'
            f'"quote_id":"{"5" * 64}",'
            '"publication_authority":"confirmed_transport"}'
        ),
        (
            '{"event":"main_post_posted","lane":"quote_image",'
            '"post_id":"9980","image_score":NaN}'
        ),
        (
            '{"event":"main_post_posted","lane":"quote_image",'
            '"post_id":"9980","image_score":1e9999}'
        ),
    ),
    ids=(
        "duplicate-main-post-id",
        "duplicate-reply-post-id",
        "duplicate-historical-reply-post-id",
        "prefixed-main-event",
        "prefixed-reply-event",
        "tab-prefixed-historical-event",
        "nonfinite-main-field",
        "overflow-main-field",
    ),
)
def test_digest_rejects_ambiguous_structured_publication_authority(
    tmp_path: Path,
    event_fragment: str,
) -> None:
    base = tmp_path / "ambiguous-structured-publication"
    write_digest_log(
        base,
        [
            "2026-08-30 21:43:52 INFO     log_event:330 - EVENT "
            + event_fragment
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["api_health"]["observed_remote_write_success_count"] == 0
    assert payload["published_reply_text_health"]["confirmed_record_count"] == 0
    assert not any(
        event.get("kind") == "confirmed_public_reply"
        for event in payload["events"]
    )


@pytest.mark.parametrize(
    ("root_overrides", "omitted_field"),
    (
        ({}, "event_version"),
        ({"event_version": 2}, None),
        ({"publication_authority": "draft"}, None),
        ({"conversation_id": "1982"}, None),
        ({"public_text": "oversize", "quote_text": "oversize"}, None),
    ),
    ids=(
        "missing-version",
        "wrong-version",
        "draft-authority",
        "wrong-conversation",
        "oversize-text",
    ),
)
def test_digest_rejects_nonauthoritative_account_root_public_text(
    tmp_path: Path,
    root_overrides: dict[str, object],
    omitted_field: str | None,
) -> None:
    base = tmp_path / "nonauthoritative-account-root-text"
    post_id = "9981"
    exact_text = "FAKE TEXT MUST NOT BECOME CONFIRMED"
    root_fields: dict[str, object] = {
        "event_version": 1,
        "lane": "quote_image",
        "post_id": post_id,
        "root_post_id": post_id,
        "conversation_id": post_id,
        "public_text": exact_text,
        "quote_text": exact_text,
        "publication_authority": "confirmed_transport",
    }
    root_fields.update(root_overrides)
    if root_fields.get("public_text") == "oversize":
        root_fields["public_text"] = "x" * 25_001
        root_fields["quote_text"] = "x" * 25_001
    if omitted_field is not None:
        root_fields.pop(omitted_field)
    write_digest_log(
        base,
        [
            digest_event_line(
                "2026-08-30 21:43:53",
                "account_root_posted",
                **root_fields,
            ),
            "2026-08-30 21:43:54 INFO post_quote_image:1001 - "
            f"Quote/image posted successfully. posted_id={post_id}",
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    posted = next(
        event
        for event in payload["events"]
        if event.get("kind") == "quote_image_posted"
    )
    assert posted["public_text_status"] == "unavailable_inconsistent"
    assert posted["public_text"] == ""
    assert posted["text"] == ""
    assert exact_text not in result.stdout


@pytest.mark.parametrize("invalid_first", (False, True))
def test_digest_invalid_structured_evidence_cannot_poison_valid_publication(
    tmp_path: Path,
    invalid_first: bool,
) -> None:
    base = tmp_path / f"order-independent-publication-{invalid_first}"
    post_id = "9986"
    quote_id = "7" * 64
    exact_text = "Exact valid account-root publication text."
    valid_main = digest_event_line(
        "2026-08-30 21:44:00",
        "main_post_posted",
        lane="quote_image",
        post_id=post_id,
        quote_hash=quote_id,
    )
    invalid_main = digest_event_line(
        "2026-08-30 21:44:01",
        "main_post_posted",
        lane="bogus",
        post_id=post_id,
        quote_hash=quote_id,
    )
    valid_root = digest_event_line(
        "2026-08-30 21:44:02",
        "account_root_posted",
        event_version=1,
        lane="quote_image",
        post_id=post_id,
        root_post_id=post_id,
        conversation_id=post_id,
        quote_id=quote_id,
        quote_text=exact_text,
        public_text=exact_text,
        publication_authority="confirmed_transport",
    )
    invalid_root = digest_event_line(
        "2026-08-30 21:44:03",
        "account_root_posted",
        event_version=1,
        lane="quote_image",
        post_id=post_id,
        root_post_id=post_id,
        conversation_id=post_id,
        quote_id=quote_id,
        quote_text="Untrusted later text",
        public_text="Untrusted later text",
        publication_authority="draft",
    )
    authority_lines = (
        [invalid_main, invalid_root, valid_main, valid_root]
        if invalid_first
        else [valid_main, valid_root, invalid_main, invalid_root]
    )
    write_digest_log(
        base,
        authority_lines
        + [
            "2026-08-30 21:44:04 INFO post_quote_image:1004 - "
            f"Quote/image posted successfully. posted_id={post_id}"
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    posted = next(
        event
        for event in payload["events"]
        if event.get("kind") == "quote_image_posted"
    )
    assert posted["public_text_status"] == "confirmed"
    assert posted["public_text"] == exact_text
    assert posted["text"] == exact_text
    assert payload["api_health"]["observed_remote_write_success_count"] == 1


def test_digest_retired_trial_metadata_does_not_affect_publication_evidence(
    tmp_path: Path,
) -> None:
    base = tmp_path / "malformed-engagement-fields"
    post_id = "9987"
    quote_id = "8" * 64
    public_text = "A genuine ordinary post remains genuine."
    write_digest_log(
        base,
        [
            digest_event_line(
                "2026-08-30 21:44:05",
                "main_post_posted",
                lane="quote_image",
                post_id=post_id,
                quote_hash=quote_id,
                engagement_experiment_id="substantive-question-v1",
                engagement_experiment_arm="treatment",
                engagement_experiment_member_position=True,
                engagement_experiment_pair_id="pair-" + "9" * 24,
                engagement_experiment_plan_sha256="9" * 64,
                engagement_experiment_publication_order="control_first",
                engagement_experiment_sequence=1.5,
                engagement_question_present="yes",
                engagement_approved_question_sha256="9" * 64,
                engagement_public_text_sha256="9" * 64,
            ),
            digest_event_line(
                "2026-08-30 21:44:06",
                "engagement_question_experimental_member_confirmed",
                post_id=post_id,
                plan_sha256=["9" * 64],
                pair_id={"value": "pair-" + "9" * 24},
                member_position=True,
                arm={"value": "treatment"},
                publication_sequence=1.5,
            ),
            digest_event_line(
                "2026-08-30 21:44:07",
                "account_root_posted",
                event_version=1,
                lane="quote_image",
                post_id=post_id,
                root_post_id=post_id,
                conversation_id=post_id,
                quote_id=quote_id,
                quote_text=public_text,
                public_text=public_text,
                publication_authority="confirmed_transport",
            ),
            "2026-08-30 21:44:08 INFO post_quote_image:1005 - "
            f"Quote/image posted successfully. posted_id={post_id}",
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "engagement_question_trial" not in payload
    posted = next(
        event
        for event in payload["events"]
        if event.get("kind") == "quote_image_posted"
    )
    assert posted["public_text_status"] == "confirmed"
    assert posted["public_text"] == public_text
    assert not any(key.startswith("engagement_") for key in posted)
    assert posted["correlation_status"] == "consistent"
    assert payload["api_health"]["observed_remote_write_success_count"] == 1


def test_digest_huge_structured_image_score_is_ignored_without_crashing(
    tmp_path: Path,
) -> None:
    base = tmp_path / "huge-structured-image-score"
    post_id = "9989"
    write_digest_log(
        base,
        [
            digest_event_line(
                "2026-08-30 21:44:13",
                "main_post_posted",
                lane="quote_image",
                post_id=post_id,
                image_score=10**400,
            )
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["api_health"]["observed_remote_write_success_count"] == 1


@pytest.mark.parametrize(
    ("production_lines", "selftest_lines", "posted_kind"),
    (
        (
            [
                "2026-08-30 21:45:00 INFO reply_lane:1100 - "
                "Considering mention id=1990 author_id=501 text='Production'",
                "2026-08-30 21:45:02 INFO create_post:1102 - Created X post "
                "successfully. response={'data': {'id': '9990'}}",
                "2026-08-30 21:45:04 INFO reply_lane:1104 - "
                "Recorded and cached own auto-reply id=9990",
                "2026-08-30 21:45:06 INFO reply_lane:1106 - "
                "Reply posted successfully",
            ],
            [
                "2026-08-30 21:45:01 INFO reply_lane:1101 - "
                "Considering mention id=1991 author_id=502 text='Selftest'",
                "2026-08-30 21:45:03 INFO create_post:1103 - Created X post "
                "successfully. response={'data': {'id': '9991'}}",
                "2026-08-30 21:45:05 INFO reply_lane:1105 - "
                "Recorded and cached own auto-reply id=9991",
            ],
            "mention_reply_posted",
        ),
        (
            [
                "2026-08-30 21:45:00 INFO reply_lane:1100 - Considering quote "
                "tweet id=1990 author_id=501 original_post_id=1880 "
                "text='Production'",
                "2026-08-30 21:45:02 INFO create_post:1102 - Created X post "
                "successfully. response={'data': {'id': '9990'}}",
                "2026-08-30 21:45:04 INFO reply_lane:1104 - Recorded and cached "
                "own quote-tweet auto-reply id=9990",
                "2026-08-30 21:45:06 INFO reply_lane:1106 - Quote-tweet reply "
                "posted successfully",
            ],
            [
                "2026-08-30 21:45:01 INFO reply_lane:1101 - Considering quote "
                "tweet id=1991 author_id=502 original_post_id=1881 "
                "text='Selftest'",
                "2026-08-30 21:45:03 INFO create_post:1103 - Created X post "
                "successfully. response={'data': {'id': '9991'}}",
                "2026-08-30 21:45:05 INFO reply_lane:1105 - Recorded and cached "
                "own quote-tweet auto-reply id=9991",
            ],
            "quote_tweet_reply_posted",
        ),
    ),
    ids=("mention", "quote-tweet"),
)
def test_digest_selftest_records_cannot_overwrite_production_reply_identity(
    tmp_path: Path,
    production_lines: list[str],
    selftest_lines: list[str],
    posted_kind: str,
) -> None:
    base = tmp_path / f"selftest-cannot-overwrite-{posted_kind}"
    base.mkdir()
    production_log = base / "application.log"
    selftest_log = base / "application.selftest.log"
    production_log.write_text(
        "\n".join(production_lines) + "\n", encoding="utf-8"
    )
    selftest_log.write_text(
        "\n".join(selftest_lines) + "\n", encoding="utf-8"
    )

    result = run_digest_inputs(base, [production_log, selftest_log])

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    posted = next(
        event
        for event in payload["events"]
        if event.get("kind") == posted_kind
    )
    assert posted["reply_post_id"] == "9990"
    assert payload["api_health"]["observed_remote_write_success_count"] == 1


@pytest.mark.parametrize(
    ("selftest_lines", "production_final", "posted_kind"),
    (
        (
            [
                "2026-08-30 21:45:10 INFO reply_lane:1110 - "
                "Considering mention id=1992 author_id=501 text='Selftest'",
                "2026-08-30 21:45:11 INFO create_post:1111 - Created X post "
                "successfully. response={'data': {'id': '9992'}}",
                "2026-08-30 21:45:12 INFO reply_lane:1112 - "
                "Recorded and cached own auto-reply id=9992",
            ],
            "2026-08-30 21:45:13 INFO reply_lane:1113 - Reply posted successfully",
            "mention_reply_posted",
        ),
        (
            [
                "2026-08-30 21:45:10 INFO reply_lane:1110 - Considering quote "
                "tweet id=1992 author_id=501 original_post_id=1882 "
                "text='Selftest'",
                "2026-08-30 21:45:11 INFO create_post:1111 - Created X post "
                "successfully. response={'data': {'id': '9992'}}",
                "2026-08-30 21:45:12 INFO reply_lane:1112 - Recorded and cached "
                "own quote-tweet auto-reply id=9992",
            ],
            "2026-08-30 21:45:13 INFO reply_lane:1113 - Quote-tweet reply posted successfully",
            "quote_tweet_reply_posted",
        ),
    ),
    ids=("mention", "quote-tweet"),
)
def test_digest_production_final_cannot_elevate_selftest_pending_identity(
    tmp_path: Path,
    selftest_lines: list[str],
    production_final: str,
    posted_kind: str,
) -> None:
    base = tmp_path / f"production-cannot-elevate-{posted_kind}"
    base.mkdir()
    selftest_log = base / "application.selftest.log"
    production_log = base / "application.log"
    selftest_log.write_text(
        "\n".join(selftest_lines) + "\n", encoding="utf-8"
    )
    production_log.write_text(production_final + "\n", encoding="utf-8")

    result = run_digest_inputs(base, [selftest_log, production_log])

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert not any(
        event.get("kind") == posted_kind for event in payload["events"]
    )
    assert payload["api_health"]["observed_remote_write_success_count"] == 0


def test_digest_selftest_pending_identity_is_not_persisted_across_resume(
    tmp_path: Path,
) -> None:
    base = tmp_path / "selftest-pending-resume"
    base.mkdir()
    state_file = base / "digest-state.json"
    selftest_log = base / "application.selftest.log"
    production_log = base / "application.log"
    selftest_log.write_text(
        "\n".join(
            [
                "2026-08-30 21:45:20 INFO reply_lane:1120 - "
                "Considering mention id=1993 author_id=501 text='Selftest'",
                "2026-08-30 21:45:21 INFO ask_grok_for_reply:1121 - "
                "Asking Grok for reply. context_text='Selftest context'",
                "2026-08-30 21:45:22 INFO reply_lane:1122 - "
                "Recorded and cached own auto-reply id=9993",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    first = run_digest_inputs(
        base,
        [selftest_log],
        state_file=state_file,
    )

    assert first.returncode == 0, first.stderr
    first_payload = json.loads(first.stdout)
    assert first_payload["resume_context"]["pending_mention"] is None
    production_log.write_text(
        "2026-08-30 21:45:23 INFO reply_lane:1123 - Reply posted successfully\n",
        encoding="utf-8",
    )
    second = run_digest_inputs(
        base,
        [production_log],
        state_file=state_file,
    )

    assert second.returncode == 0, second.stderr
    second_payload = json.loads(second.stdout)
    assert second_payload["api_health"]["observed_remote_write_success_count"] == 0
    assert not any(
        event.get("kind") == "mention_reply_posted"
        for event in second_payload["events"]
    )


def test_digest_selftest_legacy_reply_cannot_anchor_exact_text(
    tmp_path: Path,
) -> None:
    base = tmp_path / "selftest-legacy-exact-text"
    base.mkdir()
    target_id = "1994"
    reply_id = "9994"
    exact_text = "Exact production state text."
    write_json(
        base / "bot_state.json",
        {
            "daily_reply_count": 0,
            "ai_reply_history": [
                {
                    "target_id": target_id,
                    "reply_post_id": reply_id,
                    "candidate_source": "mention",
                    "proposed_reply": exact_text,
                }
            ],
            "tweet_cache": {
                reply_id: {
                    "id": reply_id,
                    "post_type": "auto_reply",
                    "text": exact_text,
                    "referenced_tweets": [
                        {"type": "replied_to", "id": target_id}
                    ],
                }
            },
        },
    )
    selftest_log = base / "application.selftest.log"
    production_log = base / "application.log"
    selftest_log.write_text(
        "\n".join(
            [
                "2026-08-30 21:45:30 INFO reply_lane:1130 - Considering "
                f"mention id={target_id} author_id=501 text='Selftest'",
                "2026-08-30 21:45:31 INFO reply_lane:1131 - Recorded and "
                f"cached own auto-reply id={reply_id}",
                "2026-08-30 21:45:32 INFO reply_lane:1132 - Reply posted successfully",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    production_log.write_text(
        digest_event_line(
            "2026-08-30 21:45:33",
            "reply_posted",
            lane="mention",
            target_id=target_id,
            reply_post_id=reply_id,
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_digest_inputs(base, [selftest_log, production_log])

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    selftest_event = next(
        event
        for event in payload["events"]
        if event.get("kind") == "mention_reply_posted"
    )
    assert "public_reply_text_status" not in selftest_event
    confirmed = next(
        event
        for event in payload["events"]
        if event.get("kind") == "confirmed_public_reply"
    )
    assert confirmed["public_reply_text"] == exact_text
    assert confirmed["public_reply_text_status"] == "confirmed"
    input_names = {
        index: Path(item["path"]).name
        for index, item in enumerate(payload["input_files"])
    }
    assert {
        input_names[reference["input_file_index"]]
        for reference in confirmed["source_refs"]
    } == {"application.log"}
    assert payload["published_reply_text_health"]["confirmed_record_count"] == 1


def test_digest_selftest_historical_anchor_cannot_activate_durable_text(
    tmp_path: Path,
) -> None:
    base = tmp_path / "selftest-historical-anchor"
    base.mkdir()
    parent_id = "1995"
    reply_id = "9995"
    quote_id = "e" * 64
    exact_text = "Durable history text requires a production anchor."
    write_private_json(
        base / "historical_context_reply_history.json",
        {
            "schema_version": 1,
            "items": {
                parent_id: {
                    "schema_version": 1,
                    "parent_post_id": parent_id,
                    "reply_post_id": reply_id,
                    "quote_id": quote_id,
                    "reply_text": exact_text,
                    "reply_epoch": 1_788_125_300,
                    "confirmed_at": "2026-08-30T20:45:00Z",
                    "status": "completed",
                }
            },
        },
    )
    selftest_log = base / "application.selftest.log"
    selftest_log.write_text(
        digest_event_line(
            "2026-08-30 21:45:40",
            "historical_context_reply",
            status="completed",
            parent_post_id=parent_id,
            quote_id=quote_id,
            character_count=len(exact_text),
            reply_preview="Durable history preview…",
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_digest_inputs(base, [selftest_log])

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    event = next(
        item
        for item in payload["events"]
        if item.get("kind") == "historical_context_reply"
    )
    assert "public_reply_text_status" not in event
    assert payload["published_reply_text_health"]["confirmed_record_count"] == 0
    assert not any(
        item.get("kind") == "confirmed_public_reply"
        for item in payload["events"]
    )


def test_digest_selftest_quote_event_cannot_anchor_production_root_text(
    tmp_path: Path,
) -> None:
    base = tmp_path / "selftest-quote-anchor"
    base.mkdir()
    post_id = "9996"
    quote_id = "f" * 64
    exact_text = "Production root text needs a production display anchor."
    production_log = base / "application.log"
    selftest_log = base / "application.selftest.log"
    production_log.write_text(
        digest_event_line(
            "2026-08-30 21:45:50",
            "account_root_posted",
            event_version=1,
            lane="quote_image",
            post_id=post_id,
            root_post_id=post_id,
            conversation_id=post_id,
            quote_id=quote_id,
            quote_text=exact_text,
            public_text=exact_text,
            publication_authority="confirmed_transport",
        )
        + "\n",
        encoding="utf-8",
    )
    selftest_log.write_text(
        "2026-08-30 21:45:51 INFO post_quote_image:1151 - "
        f"Quote/image posted successfully. posted_id={post_id}\n",
        encoding="utf-8",
    )

    result = run_digest_inputs(base, [production_log, selftest_log])

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    event = next(
        item
        for item in payload["events"]
        if item.get("kind") == "quote_image_posted"
    )
    assert "public_text_status" not in event
    assert payload["api_health"]["observed_remote_write_success_count"] == 1


def test_digest_selftest_quote_and_meme_pending_state_is_source_isolated(
    tmp_path: Path,
) -> None:
    base = tmp_path / "selftest-main-post-pending-state"
    base.mkdir()
    production_log = base / "application.log"
    selftest_log = base / "application.selftest.log"
    production_log.write_text(
        "\n".join(
            [
                "2026-08-30 21:45:52 INFO post_quote_image:1152 - "
                "Quote text='Production quote text.'",
                "2026-08-30 21:45:54 INFO post_quote_image:1154 - "
                "Quote/image posted successfully. posted_id=99961",
                "2026-08-30 21:45:56 INFO post_daily_meme:1156 - "
                "Posting meme image: /production/meme.jpg",
                "2026-08-30 21:45:57 INFO post_daily_meme:1157 - "
                "Meme image summary for cache: 'Production meme summary.'",
                "2026-08-30 21:45:59 INFO post_daily_meme:1159 - "
                "Daily meme posted successfully. posted_id=99962 "
                "file=production-meme.jpg",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    selftest_log.write_text(
        "\n".join(
            [
                "2026-08-30 21:45:53 INFO post_quote_image:1153 - "
                "Quote text='Self-test quote text.'",
                "2026-08-30 21:45:55 INFO post_quote_image:1155 - "
                "Quote/image posted successfully. posted_id=89961",
                "2026-08-30 21:45:56 INFO post_daily_meme:1156 - "
                "Posting meme image: /selftest/meme.jpg",
                "2026-08-30 21:45:58 INFO post_daily_meme:1158 - "
                "Meme image summary for cache: 'Self-test meme summary.'",
                "2026-08-30 21:46:00 INFO post_daily_meme:1160 - "
                "Daily meme posted successfully. posted_id=89962 "
                "file=selftest-meme.jpg",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_digest_inputs(base, [production_log, selftest_log])

    assert result.returncode == 0, result.stderr
    events = json.loads(result.stdout)["events"]
    quotes = {
        event["post_id"]: event
        for event in events
        if event.get("kind") == "quote_image_posted"
    }
    assert quotes["99961"]["text"] == "Production quote text."
    assert quotes["89961"]["text"] == "Self-test quote text."
    memes = {
        event["post_id"]: event
        for event in events
        if event.get("kind") == "daily_meme_posted"
    }
    assert memes["99962"]["summary"] == "Production meme summary."
    assert memes["99962"]["image"] == "/production/meme.jpg"
    assert memes["89962"]["summary"] == "Self-test meme summary."
    assert memes["89962"]["image"] == "/selftest/meme.jpg"


@pytest.mark.parametrize(
    "anchor_fragment",
    (
        (
            '{"event":"historical_context_reply","status":"completed",'
            '"parent_post_id":"100","parent_post_id":"1997",'
            '"quote_id":"' + ("1" * 64) + '","character_count":51}'
        ),
        (
            'junk {"event":"historical_context_reply","status":"completed",'
            '"parent_post_id":"1997",'
            '"quote_id":"' + ("1" * 64) + '","character_count":51}'
        ),
        (
            '{"event":"historical_context_reply","status":"completed",'
            '"parent_post_id":"1997",'
            '"quote_id":"' + ("1" * 64) + '","character_count":NaN}'
        ),
    ),
    ids=("duplicate-parent", "prefixed-envelope", "nonfinite-count"),
)
def test_digest_malformed_historical_anchor_cannot_activate_durable_text(
    tmp_path: Path,
    anchor_fragment: str,
) -> None:
    base = tmp_path / "malformed-historical-anchor"
    base.mkdir()
    parent_id = "1997"
    reply_id = "9997"
    quote_id = "1" * 64
    exact_text = "Durable exact text needs an unambiguous production anchor."
    write_private_json(
        base / "historical_context_reply_history.json",
        {
            "schema_version": 1,
            "items": {
                parent_id: {
                    "schema_version": 1,
                    "parent_post_id": parent_id,
                    "reply_post_id": reply_id,
                    "quote_id": quote_id,
                    "reply_text": exact_text,
                    "reply_epoch": 1_788_125_400,
                    "confirmed_at": "2026-08-30T20:46:40Z",
                    "status": "completed",
                }
            },
        },
    )
    write_digest_log(
        base,
        [
            "2026-08-30 21:46:40 INFO     log_event:330 - EVENT "
            + anchor_fragment
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    matching_events = [
        item
        for item in payload["events"]
        if item.get("kind") == "historical_context_reply"
    ]
    assert matching_events == []
    assert payload["published_reply_text_health"]["confirmed_record_count"] == 0
    assert exact_text not in result.stdout


def test_digest_rejects_nonfinite_and_non_utf8_structured_display_events(
    tmp_path: Path,
) -> None:
    base = tmp_path / "malformed-structured-display-events"
    write_digest_log(
        base,
        [
            "2026-08-30 21:46:41 INFO log_event:330 - EVENT "
            '{"event":"historical_context_reply","status":"dry_run",'
            '"verification_label":NaN,"confidence_dimensions":{"date":NaN}}',
            "2026-08-30 21:46:42 INFO log_event:330 - EVENT "
            '{"event":"engagement_question_experiment_invalid",'
            '"reason":NaN}',
            "2026-08-30 21:46:43 INFO log_event:330 - EVENT "
            '{"event":"author_evaluation_quarantine_started",'
            '"author_id":"200","strike_count":NaN}',
            "2026-08-30 21:46:44 INFO log_event:330 - EVENT "
            '{"event":"author_evaluation_quarantine_started",'
            '"author_id":"\\ud800","strike_count":1}',
            "2026-08-30 21:46:45 INFO log_event:330 - EVENT "
            '{"event":"mention_backlog_progress",'
            '"pages_completed":{"nested":NaN}}',
            "2026-08-30 21:46:46 INFO log_event:330 - EVENT "
            '{"event":"historical_context_outbox",'
            '"status":"failed","reason":"\\ud800"}',
            digest_event_line(
                "2026-08-30 21:46:47",
                "mention_backlog_progress",
                pages_completed=3,
                highest_mention_id="200",
            ),
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr

    def reject_constant(value: str) -> None:
        raise AssertionError(f"non-standard JSON constant leaked: {value}")

    payload = json.loads(result.stdout, parse_constant=reject_constant)
    assert [
        event["pages_completed"]
        for event in payload["events"]
        if event.get("kind") == "mention_backlog_progress"
    ] == [3]
    assert not any(
        event.get("kind")
        in {
            "historical_context_reply",
            "author_evaluation_quarantine_started",
            "historical_context_outbox",
        }
        for event in payload["events"]
    )
    assert "engagement_question_trial" not in payload


def test_digest_bounds_producer_invalid_nested_structured_display_values(
    tmp_path: Path,
) -> None:
    base = tmp_path / "nested-structured-display-values"
    private_marker = "private-nested-marker-" + ("x" * 5000)
    nested = {"private": private_marker}
    write_digest_log(
        base,
        [
            digest_event_line(
                "2026-08-30 21:46:31",
                "mention_backlog_progress",
                since_id=nested,
                pages_completed=[1],
                continuation_token_present=nested,
            ),
            digest_event_line(
                "2026-08-30 21:46:32",
                "author_evaluation_quarantine_started",
                author_id="200",
                strike_count=nested,
                quarantine_until_epoch=[1],
            ),
            digest_event_line(
                "2026-08-30 21:46:33",
                "historical_context_reply",
                status="dry_run",
                parent_post_id=nested,
                quote_id=["not-a-hash"],
                verification_label=nested,
                confidence_dimensions={"date": nested},
                reply_preview=nested,
            ),
            digest_event_line(
                "2026-08-30 21:46:34",
                "engagement_question_experiment_invalid",
                reason=nested,
                member_position=nested,
            ),
            digest_event_line(
                "2026-08-30 21:46:36",
                "historical_context_semantic_gate",
                status=nested,
                reason=nested,
                blocked_quote_count=nested,
            ),
            digest_event_line(
                "2026-08-30 21:46:37",
                "runtime_control_pause",
                key=nested,
                lanes=[nested, "quote_image"],
                until_epoch=nested,
            ),
            digest_event_line(
                "2026-08-30 21:46:38",
                "posting_transaction_state",
                parent_post_id=nested,
                main_post_state=nested,
                context_reply_state=nested,
                context_state_persisted=nested,
                reason=nested,
            ),
            digest_event_line(
                "2026-08-30 21:46:39",
                "clarification_reply_cap_override",
                target_id=nested,
                thread_id=nested,
                author_id=nested,
                bypassed_cap=nested,
            ),
            digest_event_line(
                "2026-08-30 21:46:40",
                "clarification_reply_used",
                target_id=nested,
                thread_id=nested,
                author_id=nested,
                reply_post_id=nested,
                trigger=nested,
            ),
            digest_event_line(
                "2026-08-30 21:46:41",
                "repair_reply_completed",
                target_id=nested,
                thread_id=nested,
                author_id=nested,
                reply_post_id=nested,
            ),
            digest_event_line(
                "2026-08-30 21:46:42",
                "reply_strategy_outcome",
                status=nested,
                lane=nested,
                target_id=nested,
                reply_post_id=nested,
                evidence_ids=[nested],
                no_reply_reason=nested,
            ),
            digest_event_line(
                "2026-08-30 21:46:43",
                "ai_reply_pipeline_decision",
                status=nested,
                lane=nested,
                target_id=nested,
                author_quarantine_evidence=nested,
                incoming_contribution=nested,
                proposed_draft=nested,
                repaired_draft=nested,
            ),
            digest_event_line(
                "2026-08-30 21:46:44",
                "ai_reply_pipeline_stage_summary",
                lane=nested,
                target_id=nested,
                terminal_reason=nested,
                trusted_fact_ids_supplied=[nested],
                claim_audit_outcomes=[
                    {"stage": nested, "outcome": nested}
                ],
                final_validation=nested,
            ),
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    assert private_marker not in result.stdout
    payload = json.loads(result.stdout)
    events = payload["events"]
    backlog = next(
        event
        for event in events
        if event.get("kind") == "mention_backlog_progress"
    )
    assert backlog["since_id"] is None
    assert backlog["pages_completed"] is None
    assert backlog["continuation_token_present"] is None
    quarantine = next(
        event
        for event in events
        if event.get("kind") == "author_evaluation_quarantine_started"
    )
    assert quarantine["author_id"] == "200"
    assert quarantine["strike_count"] is None
    assert quarantine["quarantine_until_epoch"] is None
    historical = next(
        event
        for event in events
        if event.get("kind") == "historical_context_reply"
    )
    assert historical["parent_post_id"] is None
    assert historical["quote_id"] is None
    assert historical["verification_label"] == "unavailable"
    assert historical["confidence_dimensions"] is None
    assert historical["reply_preview"] == ""
    assert "engagement_question_trial" not in payload
    gate = next(
        event
        for event in events
        if event.get("kind") == "historical_context_semantic_gate"
    )
    assert gate["status"] == "unavailable"
    assert gate["reason"] == ""
    pause = next(
        event
        for event in events
        if event.get("kind") == "runtime_control_pause"
    )
    assert pause["key"] == "unavailable"
    assert pause["control_lanes"] == ["quote_image"]
    transaction = next(
        event
        for event in events
        if event.get("kind") == "posting_transaction_state"
    )
    assert transaction["parent_post_id"] == ""
    assert transaction["main_post_state"] == "unavailable"
    assert transaction["context_reply_state"] == "unavailable"
    assert transaction["context_state_persisted"] is None
    assert transaction["reason"] == ""
    clarification = next(
        event
        for event in events
        if event.get("kind") == "clarification_reply_cap_override"
    )
    assert clarification["target_id"] == ""
    assert clarification["bypassed_cap"] == ""
    outcomes = [
        event
        for event in events
        if event.get("kind") == "reply_strategy_outcome"
    ]
    assert outcomes[0]["target_id"] == ""
    assert outcomes[0]["evidence_reference_count"] == 0
    decisions = [
        event
        for event in events
        if event.get("kind") == "reply_strategy_decision"
    ]
    assert decisions[0]["target_id"] == ""
    assert decisions[0]["incoming_contribution"] is None
    stage_summary = next(
        event
        for event in events
        if event.get("kind") == "reply_pipeline_stage_summary"
    )
    assert stage_summary["target_id"] == ""
    assert stage_summary["trusted_fact_ids_supplied"] == []
    assert stage_summary["claim_audit_outcomes"] == []
    assert stage_summary["final_validation"] is None


@pytest.mark.parametrize("authority_kind", ("account-root", "historical"))
def test_digest_lone_surrogate_publication_text_fails_closed(
    tmp_path: Path,
    authority_kind: str,
) -> None:
    base = tmp_path / f"lone-surrogate-{authority_kind}"
    post_id = "9998"
    quote_id = "2" * 64
    if authority_kind == "account-root":
        fragment = (
            '{"event":"account_root_posted","event_version":1,'
            '"lane":"quote_image","post_id":"9998",'
            '"root_post_id":"9998","conversation_id":"9998",'
            f'"quote_id":"{quote_id}","quote_text":"safe",'
            '"public_text":"\\ud800",'
            '"publication_authority":"confirmed_transport"}'
        )
        lines = [
            "2026-08-30 21:46:50 INFO     log_event:330 - EVENT "
            + fragment,
            "2026-08-30 21:46:51 INFO post_quote_image:1160 - "
            f"Quote/image posted successfully. posted_id={post_id}",
        ]
    else:
        fragment = (
            '{"event":"historical_context_reply_posted",'
            '"event_version":1,"lane":"historical_context_reply",'
            '"parent_post_id":"1998","reply_post_id":"9998",'
            '"root_post_id":"1998","conversation_id":"1998",'
            f'"quote_id":"{quote_id}","reply_text":"\\ud800",'
            '"publication_authority":"confirmed_transport"}'
        )
        lines = [
            "2026-08-30 21:46:50 INFO     log_event:330 - EVENT "
            + fragment
        ]
    write_digest_log(base, lines)

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["published_reply_text_health"]["confirmed_record_count"] == 0
    assert not any(
        event.get("kind") == "confirmed_public_reply"
        for event in payload["events"]
    )


def test_digest_lone_surrogate_state_text_is_unavailable_not_a_crash(
    tmp_path: Path,
) -> None:
    base = tmp_path / "lone-surrogate-state-text"
    base.mkdir()
    state = base / "bot_state.json"
    state.write_text(
        '{"ai_reply_history":[{"candidate_source":"mention",'
        '"proposed_reply":"\\ud800","reply_post_id":"9999",'
        '"target_id":"1999"}]}\n',
        encoding="utf-8",
    )
    state.chmod(0o600)
    write_digest_log(
        base,
        [
            digest_event_line(
                "2026-08-30 21:46:55",
                "reply_posted",
                lane="mention",
                target_id="1999",
                reply_post_id="9999",
            )
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    confirmed = next(
        event
        for event in payload["events"]
        if event.get("kind") == "confirmed_public_reply"
    )
    assert confirmed["public_reply_text"] is None
    assert confirmed["public_reply_text_status"] == "unavailable"
    assert payload["runtime_state_status"]["status"].startswith("malformed:")


@pytest.mark.parametrize(
    ("response_payload", "expected_legacy_id"),
    (
        ("{'data': {'id': 9967}}", "9967"),
        ("{'data': {'id': '9969'}} trailing", "9969"),
        ("not-valid {'id': '9970'}", "9970"),
    ),
    ids=("integer-id", "trailing-data", "regex-salvage"),
)
def test_digest_observed_success_rejects_noncanonical_legacy_response(
    tmp_path: Path,
    response_payload: str,
    expected_legacy_id: str,
) -> None:
    base = tmp_path / f"noncanonical-legacy-success-{expected_legacy_id}"
    write_digest_log(
        base,
        [
            "2026-08-30 21:43:55 INFO create_post:1000 - "
            f"Created X post successfully. response={response_payload}"
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    # The established legacy event remains visible, but the stricter additive
    # observed-success counter does not treat a coerced identity as authority.
    event = next(
        item
        for item in payload["events"]
        if item.get("kind") == "remote_write_succeeded"
    )
    assert event["post_id"] == expected_legacy_id
    assert payload["api_health"]["observed_remote_write_success_count"] == 0


@pytest.mark.parametrize(
    ("considering_message", "success_message", "posted_kind"),
    (
        (
            "Considering mention id=1983 author_id=501 text='Mention input'",
            "Reply posted successfully",
            "mention_reply_posted",
        ),
        (
            "Considering hot_post_reply id=1984 author_id=502 text='Hot input'",
            "Reply posted successfully",
            "hot_post_reply_posted",
        ),
        (
            "Considering quote tweet id=1985 author_id=503 "
            "original_post_id=1986 text='Quote input'",
            "Quote-tweet reply posted successfully",
            "quote_tweet_reply_posted",
        ),
    ),
    ids=("mention", "hot-post", "quote-tweet"),
)
def test_digest_derived_reply_success_rejects_noncanonical_response_identity(
    tmp_path: Path,
    considering_message: str,
    success_message: str,
    posted_kind: str,
) -> None:
    base = tmp_path / f"noncanonical-derived-{posted_kind}"
    write_digest_log(
        base,
        [
            "2026-08-30 21:43:56 INFO reply_lane:1001 - "
            + considering_message,
            "2026-08-30 21:43:57 INFO create_post:1002 - "
            "Created X post successfully. response={'data': {'id': 9982}}",
            "2026-08-30 21:43:58 INFO reply_lane:1003 - "
            + success_message,
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    posted = next(
        event
        for event in payload["events"]
        if event.get("kind") == posted_kind
    )
    assert posted["reply_post_id"] == "9982"
    assert payload["api_health"]["observed_remote_write_success_count"] == 0


def test_digest_quote_correlation_reports_all_omitted_source_refs(
    tmp_path: Path,
) -> None:
    base = tmp_path / "quote-correlation-source-ref-omissions"
    post_id = "9968"
    lines = [
        digest_event_line(
            f"2026-08-30 21:44:{offset:02d}",
            "main_post_posted",
            lane="quote_image",
            post_id=post_id,
        )
        for offset in range(10)
    ]
    lines.append(
        "2026-08-30 21:44:10 INFO post_quote_image:1005 - "
        f"Quote/image posted successfully. posted_id={post_id}"
    )
    write_digest_log(base, lines)

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    publication, = [row for row in json.loads(result.stdout)["events"]
                     if row["kind"] == "quote_image_posted"]
    assert len(publication["source_refs"]) == 8
    assert publication["source_ref_omitted_count"] == 3
    assert {
        reference["record_number"]
        for reference in publication["source_refs"]
    } == {11, *range(1, 8)}


def test_digest_published_reply_conflict_warnings_are_bounded_and_count_omissions(
    tmp_path: Path,
) -> None:
    base = tmp_path / "bounded-published-reply-warnings"
    base.mkdir()
    history = []
    confirmations = []
    for offset in range(105):
        target_id = str(2_000_000 + offset)
        reply_id = str(3_000_000 + offset)
        for suffix in ("A", "B"):
            history.append(
                {
                    "target_id": target_id,
                    "reply_post_id": reply_id,
                    "candidate_source": "mention",
                    "proposed_reply": f"Conflicting authoritative text {suffix}.",
                }
            )
        confirmations.append(
            digest_event_line(
                "2026-08-30 21:44:00",
                "reply_posted",
                lane="mention",
                target_id=target_id,
                reply_post_id=reply_id,
            )
        )
    write_json(
        base / "bot_state.json",
        {"daily_reply_count": 105, "ai_reply_history": history},
    )
    write_digest_log(base, confirmations)

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    health = json.loads(result.stdout)["published_reply_text_health"]
    assert health["conflict_count"] == 105
    assert len(health["warnings"]) == 100
    assert health["warning_omitted_count"] == 5
    assert all(len(item["reason"]) <= 240 for item in health["warnings"])


def test_digest_public_reply_text_character_bound_is_inclusive_and_explicit(
    tmp_path: Path,
) -> None:
    base = tmp_path / "published-reply-text-character-bound"
    base.mkdir()
    accepted_text = "a" * 25_000
    rejected_text = "b" * 25_001
    accepted_weighted_text = "界" * 135
    rejected_weighted_text = "界" * 136
    rejected_multiline_text = "First line\nSecond line"
    state_history = [
        {
            "target_id": "1941",
            "reply_post_id": "9941",
            "candidate_source": "mention",
            "proposed_reply": accepted_text,
        },
        {
            "target_id": "1942",
            "reply_post_id": "9942",
            "candidate_source": "mention",
            "proposed_reply": rejected_text,
        },
        {
            "target_id": "1943",
            "reply_post_id": "9943",
            "candidate_source": "mention",
            "proposed_reply": accepted_weighted_text,
        },
        {
            "target_id": "1944",
            "reply_post_id": "9944",
            "candidate_source": "mention",
            "proposed_reply": rejected_weighted_text,
        },
        {
            "target_id": "1945",
            "reply_post_id": "9945",
            "candidate_source": "mention",
            "proposed_reply": rejected_multiline_text,
        },
    ]
    write_json(
        base / "bot_state.json",
        {"daily_reply_count": 5, "ai_reply_history": state_history},
    )
    write_digest_log(
        base,
        [
            digest_event_line(
                "2026-08-30 21:45:00",
                "reply_posted",
                lane="mention",
                target_id="1941",
                reply_post_id="9941",
            ),
            digest_event_line(
                "2026-08-30 21:45:01",
                "reply_posted",
                lane="mention",
                target_id="1942",
                reply_post_id="9942",
            ),
            digest_event_line(
                "2026-08-30 21:45:02",
                "reply_posted",
                lane="mention",
                target_id="1943",
                reply_post_id="9943",
            ),
            digest_event_line(
                "2026-08-30 21:45:03",
                "reply_posted",
                lane="mention",
                target_id="1944",
                reply_post_id="9944",
            ),
            digest_event_line(
                "2026-08-30 21:45:04",
                "reply_posted",
                lane="mention",
                target_id="1945",
                reply_post_id="9945",
            ),
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    events = {
        event["reply_post_id"]: event
        for event in payload["events"]
        if event.get("kind") == "confirmed_public_reply"
    }
    accepted = events["9941"]
    assert accepted["public_reply_text"] == accepted_text
    assert accepted["public_reply_text_character_count"] == 25_000
    assert accepted["public_reply_text_complete"] is True
    assert accepted["public_reply_text_status"] == "confirmed"

    accepted_weighted = events["9943"]
    assert accepted_weighted["public_reply_text"] == accepted_weighted_text
    assert accepted_weighted["public_reply_text_character_count"] == 135
    assert accepted_weighted["public_reply_text_complete"] is True
    assert accepted_weighted["public_reply_text_status"] == "confirmed"

    for reply_post_id in ("9942",):
        rejected = events[reply_post_id]
        assert rejected["public_reply_text"] is None
        assert rejected["public_reply_text_character_count"] is None
        assert rejected["public_reply_text_complete"] is False
        assert rejected["public_reply_text_status"] == "conflict"
        assert "conversational public-text contract" in rejected[
            "public_reply_text_reason"
        ].lower()
        assert len(rejected["public_reply_text_reason"]) <= 320

    for reply_post_id, expected_text in (
        ("9944", rejected_weighted_text),
        ("9945", rejected_multiline_text),
    ):
        valid_long_post = events[reply_post_id]
        assert valid_long_post["public_reply_text"] == expected_text
        assert valid_long_post["public_reply_text_complete"] is True
        assert valid_long_post["public_reply_text_status"] == "confirmed"


def test_digest_observed_transport_subtotals_cover_main_and_reply_lanes(
    tmp_path: Path,
) -> None:
    base = tmp_path / "observed-transport-main-reply-subtotals"
    lanes = (
        ("quote_image", "None"),
        ("daily_meme", "None"),
        ("conversational_reply", "1951"),
        ("historical_context_reply", "1952"),
        ("mention", "1953"),
        ("quote_tweet", "1954"),
    )
    lines = []
    for offset, (lane, reply_to_id) in enumerate(lanes):
        transaction_id = hashlib.sha256(lane.encode("ascii")).hexdigest()
        line = (
            f"2026-08-30 21:46:{offset:02d} INFO create_post:{900 + offset} - "
            "Creating X post with durable transport journal. "
            f"lane={lane} transaction_id={transaction_id} "
            f"reply_to_id={reply_to_id} media_count=0 made_with_ai=False"
        )
        lines.append(line)
        if lane == "quote_image":
            lines.append(line)
    write_digest_log(base, lines)

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    health = json.loads(result.stdout)["api_health"]
    assert health["observed_tweet_transport_request_count"] == 6
    assert health["observed_tweet_transport_main_post_request_count"] == 2
    assert health["observed_tweet_transport_reply_request_count"] == 4
    assert (
        health["observed_tweet_transport_main_post_request_count"]
        + health["observed_tweet_transport_reply_request_count"]
        == health["observed_tweet_transport_request_count"]
    )
    assert health["observed_tweet_transport_request_counts_by_lane"] == {
        lane: 1 for lane, _reply_to_id in sorted(lanes)
    }
    assert health["observed_tweet_transport_unclassified_request_count"] == 0
    for field in (
        "observed_tweet_transport_request_counts_by_lane",
        "observed_tweet_transport_main_post_request_count",
        "observed_tweet_transport_reply_request_count",
        "observed_tweet_transport_unclassified_request_count",
    ):
        assert field in health["counter_semantics"]


@pytest.mark.parametrize("reverse_order", (False, True))
def test_digest_transport_lane_conflict_is_order_independent_and_unclassified(
    tmp_path: Path,
    reverse_order: bool,
) -> None:
    base = tmp_path / f"transport-lane-conflict-{reverse_order}"
    transaction_id = "f" * 64
    first_lane, second_lane = (
        ("mention", "quote_image")
        if reverse_order
        else ("quote_image", "mention")
    )
    records = [
        "2026-08-30 21:46:20 INFO create_post:920 - "
        "Creating X post with durable transport journal. "
        f"lane={first_lane} transaction_id={transaction_id} "
        "reply_to_id=None media_count=1 made_with_ai=False",
        "2026-08-30 21:46:21 INFO create_post:921 - "
        "Creating X post with durable transport journal. "
        f"lane={second_lane} transaction_id={transaction_id} "
        "reply_to_id=1955 media_count=0 made_with_ai=True",
    ]
    write_digest_log(base, records)

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    health = json.loads(result.stdout)["api_health"]
    assert health["observed_tweet_transport_request_count"] == 1
    assert health["observed_tweet_transport_request_counts_by_lane"] == {
        "conflicted": 1
    }
    assert health["observed_tweet_transport_main_post_request_count"] == 0
    assert health["observed_tweet_transport_reply_request_count"] == 0
    assert health["observed_tweet_transport_unclassified_request_count"] == 1
    assert health["observed_tweet_transport_lane_conflict_count"] == 1
    assert health[
        "observed_tweet_transport_lane_conflict_transaction_ids"
    ] == [transaction_id]


def test_digest_observed_transport_unknown_lane_is_explicitly_unclassified(
    tmp_path: Path,
) -> None:
    base = tmp_path / "observed-transport-unclassified-lane"
    transaction_id = hashlib.sha256(b"future-publication-lane").hexdigest()
    write_digest_log(
        base,
        [
            "2026-08-30 21:46:30 INFO create_post:950 - "
            "Creating X post with durable transport journal. "
            f"lane=future_publication_lane transaction_id={transaction_id} "
            "reply_to_id=None media_count=0 made_with_ai=False"
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    health = json.loads(result.stdout)["api_health"]
    assert health["observed_tweet_transport_request_count"] == 1
    assert health["observed_tweet_transport_main_post_request_count"] == 0
    assert health["observed_tweet_transport_reply_request_count"] == 0
    assert health["observed_tweet_transport_unclassified_request_count"] == 1
    assert health["observed_tweet_transport_request_counts_by_lane"] == {
        "future_publication_lane": 1
    }
    assert (
        health["observed_tweet_transport_main_post_request_count"]
        + health["observed_tweet_transport_reply_request_count"]
        + health["observed_tweet_transport_unclassified_request_count"]
        == health["observed_tweet_transport_request_count"]
    )


def test_digest_media_upload_success_is_durable_handoff_not_request_or_post_success(
    tmp_path: Path,
) -> None:
    base = tmp_path / "observed-media-upload-success"
    attempt_id = "a" * 64
    media_id = "9961"
    transaction_id = "f" * 64
    confirmed_handoff = (
        "2026-08-30 21:47:01 INFO upload_media:1001 - "
        "Handed confirmed media upload to durable main-post attempt "
        f"lane=quote_image attempt_id={attempt_id} media_id={media_id}"
    )
    write_digest_log(
        base,
        [
            "2026-08-30 21:47:00 INFO upload_media:1000 - "
            "Uploading receipt-bound media via X API v2: /tmp/t99.jpg",
            confirmed_handoff,
            confirmed_handoff,
            "2026-08-30 21:47:02 INFO create_post:1002 - "
            "Creating X post with durable transport journal. "
            f"lane=quote_image transaction_id={transaction_id} "
            "reply_to_id=None media_count=1 made_with_ai=False",
            "2026-08-30 21:47:03 INFO create_post:1003 - "
            "Created X post successfully. response={'data': {'id': '9962'}}",
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    health = json.loads(result.stdout)["api_health"]
    assert health["observed_media_upload_request_count"] == 1
    assert health["observed_media_upload_success_count"] == 1
    assert health["observed_tweet_transport_request_count"] == 1
    assert health["observed_remote_write_success_count"] == 1
    semantics = health["counter_semantics"][
        "observed_media_upload_success_count"
    ]
    semantics_text = json.dumps(semantics).lower()
    assert "durable" in semantics_text
    assert "handoff" in semantics_text
    assert "request" in semantics_text
    assert "post" in semantics_text


def test_digest_reply_enrichment_uses_bounded_event_index_scans() -> None:
    """Guard scaling structurally without a timing-sensitive performance test."""

    import mrs_log_digest as digest_module

    class CountingEvents(list[dict[str, object]]):
        def __init__(self, values: list[dict[str, object]]) -> None:
            super().__init__(values)
            self.iteration_count = 0

        def __iter__(self):  # type: ignore[no-untyped-def]
            self.iteration_count += 1
            return super().__iter__()

    event_count = 256
    events = CountingEvents(
        [
            {
                "kind": "mention_reply_posted",
                "time": "2026-08-30 21:48:00",
                "mention_id": str(4_000_000 + offset),
                "reply_post_id": str(5_000_000 + offset),
            }
            for offset in range(event_count)
        ]
    )
    confirmations = [
        {
            "time": "2026-08-30 21:48:01",
            "lane": "mention",
            "target_id": str(4_000_000 + offset),
            "reply_post_id": str(5_000_000 + offset),
            "_event_insertion_index": offset,
            "_source_sequence": offset,
        }
        for offset in range(event_count)
    ]
    report: dict[str, object] = {"events": events}

    digest_module.enrich_published_reply_text(
        report,
        runtime_state={},
        structured_reply_confirmations=confirmations,
        historical_reply_text_evidence=[],
    )

    assert len(events) == event_count
    assert events.iteration_count <= 12


def test_digest_api_counter_scopes_describe_patterns_not_log_level_filters(
    tmp_path: Path,
) -> None:
    """Counter metadata must match the parser, which does not inspect level."""
    base = tmp_path / "api-counter-message-pattern-semantics"
    transaction_id = "e" * 64
    write_digest_log(
        base,
        [
            "2026-08-30 21:40:00 WARNING x_request:801 - "
            "X request: POST https://api.x.com/2/tweets",
            "2026-08-30 21:40:01 WARNING x_request:802 - "
            "X request: POST https://api.x.com/2/media/upload",
            "2026-08-30 21:40:02 WARNING create_post:803 - "
            "Creating X post with durable transport journal. "
            f"lane=mention transaction_id={transaction_id} reply_to_id=1901 "
            "media_count=1 made_with_ai=True",
            "2026-08-30 21:40:03 WARNING upload_media:804 - "
            "Uploading receipt-bound media via X API v2: /tmp/t01.jpg",
        ],
    )

    result = run_digest(base, as_json=True)

    assert result.returncode == 0, result.stderr
    health = json.loads(result.stdout)["api_health"]
    assert health["tweet_create_request_count"] == 1
    assert health["media_upload_request_count"] == 1
    assert health["observed_tweet_transport_request_count"] == 1
    assert health["observed_media_upload_request_count"] == 1
    semantics = health["counter_semantics"]
    for field in (
        "tweet_create_request_count",
        "media_upload_request_count",
        "observed_tweet_transport_request_count",
        "observed_media_upload_request_count",
    ):
        scope = semantics[field]["scope"].lower()
        assert "debug" not in scope
        assert "info" not in scope
        assert "log level" in scope
        assert "regardless" in scope or "not filtered" in scope


def quote_search_requests(server: FakeApiServer) -> list[dict]:
    """Return quote-search requests separately from hot-post conversation searches."""
    return [request for request in server.requests
            if request["path"] == "/2/tweets/search/recent"
            and "quotes_of_tweet_id:" in request["query"].get("query", [""])[0]]


def test_combined_five_post_search_preserves_watch_priority_and_restart_dedupe(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    template = scenario["quote_tweets"]["900"]["data"][0]
    parents = ["900", "901", "902", "903", "904"]
    scenario["tweets"] = {parent: {"id": parent, "author_id": "12345", "text": "Original post " + parent} for parent in parents}
    scenario["quote_tweets"] = {
        parent: {"data": [dict(template, id=target, conversation_id=target,
                              author_id=author, referenced_tweets=[{"type": "quoted", "id": parent}])]}
        for parent, target, author in [("900", "910", "700"), ("904", "920", "701")]
    }
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path, watch_ids=["904"],
            state={"recent_own_post_ids": parents, "next_reply_lane_priority": "quote"},
            local_config={"ENABLE_HOT_POST_REPLY_CHECKS": False, "MIN_SECONDS_BETWEEN_REPLIES": 1},
        )
        first = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"})
        assert first.returncode == 0, first.stderr + first.stdout
        assert fake_server_post_replies(server) == ["920"]
        requests = quote_search_requests(server)
        assert len(requests) == 1
        assert requests[0]["query"]["query"] == ["(" + " OR ".join("quotes_of_tweet_id:" + parent for parent in parents) + ") -is:retweet"]
        assert not any(request["path"].endswith("/quote_tweets") for request in server.requests)
        assert not any(request["path"] in ["/2/tweets/901", "/2/tweets/902", "/2/tweets/903"] for request in server.requests)
        assert set(read_json(base_dir / "bot_state.json")["quote_pending_candidates"]) == {"910"}
        second = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000002"})
        assert second.returncode == 0, second.stderr + second.stdout
        assert fake_server_post_replies(server) == ["920", "910"]
        assert len(quote_search_requests(server)) == 1
        state = read_json(base_dir / "bot_state.json")
        assert state["quote_pending_candidates"] == {}
        assert set(state["replied_to_quote_post_ids"]) == {"910", "920"}
        assert state["daily_quote_reply_count"] == 2
    finally:
        server.stop()


def test_truncated_combined_search_cannot_hide_priority_original(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    template = scenario["quote_tweets"]["900"]["data"][0]
    parents = ["900", "901", "902", "903", "904"]
    scenario["enable_pagination"] = True
    scenario["tweets"] = {parent: {"id": parent, "author_id": "12345", "text": "Original post " + parent} for parent in parents}
    scenario["quote_tweets"] = {
        "900": {"data": [dict(template, id="910", conversation_id="910")]},
        "904": {"data": [dict(template, id=str(2000 + i), conversation_id=str(2000 + i),
                               referenced_tweets=[{"type": "quoted", "id": "904"}]) for i in range(150)]},
    }
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path, watch_ids=["900"],
            state={"recent_own_post_ids": parents, "next_reply_lane_priority": "quote"},
            local_config={"ENABLE_HOT_POST_REPLY_CHECKS": False, "MIN_SECONDS_BETWEEN_REPLIES": 1},
        )
        result = run_bot_command(base_dir, server, "--test-cycle", extra_env={"MRS_FAKE_NOW_EPOCH": "2000000000"})
        assert result.returncode == 0, result.stderr + result.stdout
        assert fake_server_post_replies(server) == ["910"]
        requests = quote_search_requests(server)
        assert len(requests) == 22
        assert " OR " in requests[0]["query"]["query"][0]
        assert requests[15]["query"]["query"] == ["(quotes_of_tweet_id:900) -is:retweet"]
        state = read_json(base_dir / "bot_state.json")
        assert state["quote_search_pagination_tokens"] == {"(quotes_of_tweet_id:904) -is:retweet": "30"}
    finally:
        server.stop()


@pytest.mark.parametrize("lane", ["mention", "quote", "hot_post"])
def test_full_post_text_reaches_real_reply_payload_across_discovery_lanes(tmp_path: Path, lane: str) -> None:
    """A question beyond the short excerpt survives discovery through model input."""
    full = "Background to the question. " * 20 + "Which particular policy is being discussed at the end?"
    target = {
        "id": "910", "text": "Only the short opening is here.", "author_id": "310",
        "conversation_id": "910", "created_at": "2026-06-30T12:00:00Z",
        "note_tweet": {"text": full, "entities": {"mentions": []}},
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
    }
    subject = {"id": "900", "text": "The short subject excerpt.", "author_id": "12345", "conversation_id": "900",
               "note_tweet": {"text": "The complete subject provides all the context. " * 10}}
    scenario = {"tweets": {"900": subject}, "grok_replies": ["I appreciate the distinction."]}
    options = {}
    if lane == "mention":
        scenario["mentions"] = [target]
    elif lane == "quote":
        target["referenced_tweets"] = [{"type": "quoted", "id": "900"}]
        scenario["quote_tweets"] = {"900": {"data": [target]}}
        options["state"] = {"recent_own_post_ids": ["900"], "next_reply_lane_priority": "quote"}
    else:
        target["conversation_id"] = "900"
        target["referenced_tweets"] = [{"type": "replied_to", "id": "900"}]
        scenario["search_recent"] = [target]
        options["watch_ids"] = ["900"]
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(tmp_path, **options)
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        assert len(server.openai_requests) == 1
        payload = json.loads(server.openai_requests[0]["input"])
        assert payload["visible_conversation"][-1]["text"] == full
        if lane in {"quote", "hot_post"}:
            assert payload["visible_conversation"][0]["text"] == subject["note_tweet"]["text"].strip()
        assert len(server.posts) == 1
        state = read_json(base_dir / "bot_state.json")
        assert state["ai_reply_history"][-1]["incoming_contribution"] == full
        for request in server.requests:
            if request["method"] == "GET" and "tweet.fields" in request["query"]:
                assert "note_tweet" in request["query"]["tweet.fields"][0].split(",")
    finally:
        server.stop()


def test_full_post_text_refreshes_legacy_queue_and_parent_after_restart(tmp_path: Path) -> None:
    """A saved short excerpt must not survive restart into model context."""
    full_target = "My earlier explanation. " * 20 + "The specific measure is the British Nationality Act 1981."
    full_parent = "Earlier context. " * 20 + "The final distinction concerns individual circumstances."
    parent = {"id": "99", "author_id": "12345", "conversation_id": "99", "text": "Earlier excerpt", "note_tweet": {"text": full_parent}}
    target = {"id": "100", "author_id": "200", "conversation_id": "99", "text": "@MrsMThatcher Short excerpt",
              "referenced_tweets": [{"type": "replied_to", "id": "99"}],
              "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
              "note_tweet": {"text": full_target}}
    old_parent = {key: value for key, value in parent.items() if key != "note_tweet"}
    old_parent["cached_epoch"] = 2_000_000_000
    old_target = {key: value for key, value in target.items() if key != "note_tweet"}
    scenario = {"tweets": {"99": parent, "100": target}, "grok_replies": ["Thank you for the observation."]}
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(tmp_path, state={
            "last_seen_mention_id": "100", "mention_pending_candidates": {"100": old_target},
            "tweet_cache": {"99": old_parent},
        })
        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        assert len(server.openai_requests) == 1
        payload = json.loads(server.openai_requests[0]["input"])
        assert [turn["text"] for turn in payload["visible_conversation"]] == [full_parent, full_target]
        state = read_json(base_dir / "bot_state.json")
        assert state["tweet_cache"]["99"]["text"] == full_parent
        assert state["tweet_cache"]["99"]["text_is_complete"] is True
        assert state["ai_reply_history"][-1]["incoming_contribution"] == full_target
        assert len(server.posts) == 1
    finally:
        server.stop()

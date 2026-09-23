"""Create isolated bot assets and run subprocess cycles against a local fake API."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from historical_context_source_recovery import RECOVERY_FILENAME, recover_saved_source_evidence
from historical_context_source_roles import AUDIT_FILENAME, build_audit
from remote_write_safety_protocol import (
    ACTIVATION_BASENAME as REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_BASENAME,
)
from tests.fake_api_server import FakeApiServer
from tests.helpers.protocol_activation import create_test_protocol_activation


ROOT = Path(__file__).resolve().parents[2]
BOT = ROOT / "mrsMThatcher2.py"
SCENARIOS = ROOT / "tests" / "fixtures" / "scenarios"


def collapse_quote_whitespace(text: str) -> str:
    """Normalise fixture quotation text before hashing it."""
    return " ".join(str(text or "").split())


def sha256_bytes(value: bytes) -> str:
    """Return the digest used to bind synthetic fixture files."""
    return hashlib.sha256(value).hexdigest()


def write_minimal_asset_analysis(base_dir: Path) -> None:
    """Write a self-contained quotation corpus and image analysis for a test bot."""
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
    write_json(research_dir / "grounding_sources.json", {})
    recovery = recover_saved_source_evidence(
        research_dir,
        {quote_hash: packet},
    )
    write_json(research_dir / RECOVERY_FILENAME, recovery)
    source_role_audit = build_audit(
        {quote_hash: packet},
        set(),
        research_dir=research_dir,
        attribution_eligible_ids={quote_hash},
        recovered_evidence=recovery,
        audit_date="2030-01-01",
    )
    write_json(research_dir / AUDIT_FILENAME, source_role_audit)
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
    """Read one UTF-8 JSON fixture."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict) -> None:
    """Write a deterministic JSON fixture."""
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


def persist_test_runtime_state(base_dir: Path, changes: dict) -> None:
    """Change isolated runtime state through its real locked generation writer."""
    env = base_test_env()
    env.update({"MRS_TEST_MODE": "1", "MRS_BASE_DIR": str(base_dir),
                "MRS_LOG_FILE": str(base_dir / "fixture-update.log")})
    script = "\n".join((
        "import json, sys",
        "import mrsMThatcher2 as bot",
        "bot.apply_local_config()",
        "bot.acquire_instance_lock()",
        "state = bot.load_state()",
        "state.update(json.load(sys.stdin))",
        "bot.save_state(state, durable=True)",
    ))
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, env=env,
        input=json.dumps(changes, allow_nan=False), text=True,
        capture_output=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def base_test_env() -> dict[str, str]:
    """Copy the isolated test environment with the bot timezone selected."""
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
    """Create an isolated bot directory with configurable assets, state and controls."""
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
        "single_call_reply": {
            "enabled": True,
            "strategy_version": "single-sol-reply-20260904",
            "model": "gpt-6-sol",
            "timeout_seconds": 10,
        },
    }
    if local_config:
        config.update(local_config)
    write_json(base_dir / "mrsMThatcher.local.json", config)

    # Model a pre-generation installation when tests supply an unsealed runtime
    # dictionary. Current generation documents must be produced by the writer.
    fixture_state = dict(state or {})
    if "_state_generation" not in fixture_state:
        if fixture_state.get("minimum_reader_version", 5) == 5:
            fixture_state["minimum_reader_version"] = 4
        if fixture_state.get("pending_reply_drafts") in (None, {}, {"__mrs_state_reader_compatibility_fence__": 5}):
            fixture_state["pending_reply_drafts"] = {
                "__mrs_state_reader_compatibility_fence__": 4,
            }
    write_private_json(base_dir / "bot_state.json", fixture_state)
    write_private_json(base_dir / "lines_used.json", [])
    write_private_json(base_dir / "images_used.json", [])
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
    openai_api_base_url: str | None = None,
    unset_x_upload_base_url: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one bot command against the supplied local fake API server."""
    env = base_test_env()
    env.update(
        {
            "MRS_TEST_MODE": "1",
            "MRS_BASE_DIR": str(base_dir),
            "MRS_LOG_FILE": str(base_dir / "test.log"),
            "X_API_BASE_URL": x_api_base_url or server.url,
            "X_UPLOAD_BASE_URL": x_upload_base_url or server.url,
            "XAI_API_BASE_URL": xai_api_base_url or f"{server.url}/v1",
            "OPENAI_API_BASE_URL": openai_api_base_url or f"{server.url}/v1",
            "X_CONSUMER_KEY": "dummy",
            "X_CONSUMER_SECRET": "dummy",
            "X_ACCESS_TOKEN": "dummy",
            "X_ACCESS_SECRET": "dummy",
            "X_MY_USER_ID": "12345",
            "XAI_API_KEY": "dummy",
            "OPENAI_API_KEY": "dummy",
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


def run_cycle(base_dir: Path, server: FakeApiServer) -> subprocess.CompletedProcess[str]:
    """Run one normal bot test cycle against the supplied fake server."""
    return run_bot_command(base_dir, server, "--test-cycle")

"""Shared isolated snapshots and bot sessions for offline selector tests."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.helpers.bot_runtime import bot
from tools import simulate_regular_post_futures as sim

ROOT = Path(__file__).resolve().parents[2]

DETERMINISTIC_FLAGS = {
    "ENABLE_GENERATED_IMAGE_POOL": True,
    "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST": 6,
    "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": 2,
    "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING": True,
    "ORIGINAL_EDITORIAL_SHADOW_WEIGHT": 0.32,
    "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT": 4.0,
    "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING": True,
    "ENABLE_GENERATED_IDENTITY_POLICY_SCORING": False,
    "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY": 6.0,
    "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY": 15.0,
}


def build_simulator_snapshot(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create private mutable selector state beside shared static evidence."""
    snapshot = tmp_path_factory.mktemp("regular-selector-snapshot")
    static_files = (
        "mrsMThatcher.txt",
        "quote_analysis.json",
        "image_analysis.json",
        "generated_image_analysis.json",
        "original_image_editorial_analysis_experiment_v1.json",
        "generated_image_identity_dependence_audit.json",
    )
    for name in static_files:
        (snapshot / name).symlink_to(ROOT / name)
    (snapshot / "research_packets.json").symlink_to(
        ROOT / "semantic_alignment_research/quote_research_full_001/research_packets.json"
    )
    (snapshot / "corpus_manifest.json").symlink_to(
        ROOT
        / "semantic_alignment_research"
        / "quote_research_full_001"
        / "corpus_manifest.json"
    )
    final_unresolved = snapshot / "final_unresolved"
    final_unresolved.mkdir()
    (final_unresolved / "final_research_status.json").symlink_to(
        ROOT
        / "semantic_alignment_research"
        / "quote_research_full_001"
        / "final_unresolved"
        / "final_research_status.json"
    )
    (snapshot / "runtime_eligible_quote_manifest.json").symlink_to(
        ROOT
        / "semantic_alignment_research"
        / "quote_attribution_cleanup_001"
        / "deployment_candidate"
        / "runtime_eligible_quote_manifest.json"
    )
    (snapshot / "images").symlink_to(ROOT / "images", target_is_directory=True)
    (snapshot / "generated_images").symlink_to(
        ROOT / "generated_review_approved_images",
        target_is_directory=True,
    )
    (snapshot / "mrsMThatcher.local.json").write_text(
        json.dumps(DETERMINISTIC_FLAGS),
        encoding="utf-8",
    )
    state = bot.default_state()
    state["original_regular_posts_since_generated_image"] = (
        DETERMINISTIC_FLAGS["GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN"]
    )
    (snapshot / "bot_state.json").write_text(json.dumps(state), encoding="utf-8")
    (snapshot / "images_used.json").write_text("[]", encoding="utf-8")
    (snapshot / "lines_used.json").write_text("[]", encoding="utf-8")
    return snapshot


@contextmanager
def isolated_simulator_bot(tmp_path: Path, snapshot: Path):
    """Bind the shared bot to a simulation snapshot and restore every change."""
    image_policy = sim.historical_image_selection(bot)
    touched = set(bot.LOCAL_CONFIG_ALLOWED_KEYS) | set(image_policy.CONFIG_DEFAULTS) | {
        "LINES_FILE", "QUOTE_ANALYSIS_FILE", "IMAGE_ANALYSIS_FILE", "GENERATED_IMAGE_ANALYSIS_FILE",
        "HISTORICAL_CONTEXT_RESEARCH_DIR", "COMPLETED_QUOTE_RESEARCH_FILE",
        "RUNTIME_ELIGIBLE_QUOTE_MANIFEST_FILE",
        "IMAGE_GLOB", "GENERATED_IMAGE_DIR", "GENERATED_IMAGE_GLOB", "ORIGINAL_EDITORIAL_ANALYSIS_FILE",
        "GENERATED_IDENTITY_AUDIT_FILE", "STATE_FILE", "IMAGES_USED_FILE", "LINES_USED_FILE",
        "REGULAR_POST_RECEIPT_FILE", "MEME_POST_RECEIPT_FILE", "CONFIRMED_REPLY_RECEIPT_FILE", "LOCK_FILE",
        "_ORIGINAL_EDITORIAL_ANALYSIS_CACHE", "_GENERATED_IDENTITY_AUDIT_CACHE", "load_quote_analysis",
        "load_image_analysis", "current_image_sha256", "now_epoch", "upload_media", "create_post",
        "acquire_instance_lock", "post_random_quote", "post_next_meme",
        "maybe_reply_to_mentions", "maybe_reply_to_quote_tweets", "write_regular_post_receipt",
        "write_meme_post_receipt", "remove_regular_post_receipt",
        "remove_meme_post_receipt", "_reply_assembly", "atomic_write_json", "save_used_set",
        "save_quote_used_hashes", "save_image_used_basenames", "save_state",
        "completed_research_quote_hashes", "_quote_candidates_owner",
    }
    original = {
        name: (image_policy, getattr(image_policy, name))
        if hasattr(image_policy, name) else (bot, getattr(bot, name))
        for name in touched
    }
    request_names = ("request", "get", "post", "put", "patch", "delete")
    original_requests = {name: getattr(bot.requests, name) for name in request_names}
    rng_state = bot.random.getstate()
    try:
        sim.apply_snapshot_config(bot, snapshot)
        bot.LINES_FILE = snapshot / "mrsMThatcher.txt"
        bot.HISTORICAL_CONTEXT_RESEARCH_DIR = snapshot
        bot.COMPLETED_QUOTE_RESEARCH_FILE = snapshot / "research_packets.json"
        bot.RUNTIME_ELIGIBLE_QUOTE_MANIFEST_FILE = (
            snapshot / "runtime_eligible_quote_manifest.json"
        )
        validated_eligible_ids = frozenset(
            bot.load_completed_research_quote_hashes()
        )
        bot.completed_research_quote_hashes = (
            lambda: set(validated_eligible_ids)
        )
        candidate_owner_factory = bot._quote_candidates_owner

        class SnapshotQuoteCandidates(type(candidate_owner_factory())):
            def completed(self):
                return set(validated_eligible_ids)

        bot._quote_candidates_owner = lambda **kwargs: SnapshotQuoteCandidates(
            **vars(candidate_owner_factory(**kwargs))
        )
        yield bot
    finally:
        for name, (owner, value) in original.items():
            setattr(owner, name, value)
        for name, value in original_requests.items():
            setattr(bot.requests, name, value)
        bot.random.setstate(rng_state)


def run_private_future(
    private_bot,
    directory: Path,
    snapshot: Path,
    *,
    posts: int,
    detail: str = "none",
    resume: bool = False,
    failure_hook=None,
    seed: int = 77_123,
    start_epoch: int = 1_788_453_600,
) -> list[dict]:
    """Run a deterministic future using only the supplied private directory."""
    directory.mkdir(parents=True, exist_ok=True)
    writer = sim.PrivateWriter(directory)
    return sim.run_future(
        private_bot,
        writer,
        directory,
        snapshot,
        "equivalence-session",
        0,
        seed,
        posts,
        start_epoch,
        detail,
        resume,
        failure_hook=failure_hook,
    )

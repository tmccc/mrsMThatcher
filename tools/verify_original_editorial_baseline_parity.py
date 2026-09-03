#!/usr/bin/env python3
"""Compare the base selector with editorial disabled, shadow, and fail-closed production.

The controller starts an isolated Python process for each implementation/mode.
Each worker imports the requested repository tree in ``MRS_TEST_MODE``, blocks
network sockets, redirects every mutable path to a temporary directory, and
runs deterministic calls through the real quote/image selector.  Worker files
are deliberately temporary; the durable JSON report contains only hashes,
counts, coverage, and bounded mismatch examples.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import logging
import os
import random
import re
import socket
import subprocess
import sys
import tempfile
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
HARNESS_VERSION = "original-editorial-baseline-parity-v1"
DEFAULT_COUNT = 5_000
SCENARIOS = (
    "normal_original",
    "normal_mixed",
    "generated_spacing_blocked",
    "forced_cycle_reset",
    "last_image_fallback",
    "equal_baseline_tie",
    "used_cycle_transition",
    "seasonal_exclusion",
    "stale_exclusion",
    "visual_mismatch_exclusion",
    "origin_quote_match",
    "identity_policy_enabled",
    "no_candidate_outcome",
)
RELEVANT_EVENT_PREFIXES = (
    "ORIGINAL_EDITORIAL_SHADOW_RESULT ",
    "GENERATED_IDENTITY_POLICY_SHADOW_RESULT ",
    "GENERATED_IDENTITY_POLICY_APPLIED_RESULT ",
)


class ParityError(RuntimeError):
    """Raised when the parity harness cannot make a trustworthy comparison."""


def canonical_json(value: object) -> bytes:
    """Return strict canonical JSON bytes."""
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def value_sha256(value: object) -> str:
    """Return the SHA-256 of a JSON-compatible value."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def file_sha256(path: Path) -> str:
    """Return one file's SHA-256."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@contextmanager
def blocked_network() -> Iterable[None]:
    """Reject all socket activity in a worker."""
    originals = (
        socket.create_connection,
        socket.socket.connect,
        socket.socket.connect_ex,
        socket.socket.sendto,
    )

    def fail(*_args: object, **_kwargs: object) -> Any:
        raise ParityError("network access is forbidden in the parity harness")

    socket.create_connection = fail
    socket.socket.connect = fail
    socket.socket.connect_ex = fail
    socket.socket.sendto = fail
    try:
        yield
    finally:
        (
            socket.create_connection,
            socket.socket.connect,
            socket.socket.connect_ex,
            socket.socket.sendto,
        ) = originals


class EventCapture(logging.Handler):
    """Capture only stable pre-existing structured selector events."""

    def __init__(self) -> None:
        """Initialise an empty bounded worker-local event capture."""
        super().__init__(logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Retain one relevant structured log message."""
        message = record.getMessage()
        if message.startswith(RELEVANT_EVENT_PREFIXES):
            self.messages.append(message)


def import_bot(root: Path, runtime: Path) -> Any:
    """Import one repository's bot module under an isolated module name."""
    env = {
        "MRS_TEST_MODE": "1",
        "MRS_BASE_DIR": str(runtime),
        "MRS_LOG_FILE": str(runtime / "import.log"),
        "LOG_LEVEL": "CRITICAL",
        "X_API_BASE_URL": "http://127.0.0.1:9",
        "X_UPLOAD_BASE_URL": "http://127.0.0.1:9",
        "XAI_API_BASE_URL": "http://127.0.0.1:9/v1",
        "X_CONSUMER_KEY": "parity-disabled",
        "X_CONSUMER_SECRET": "parity-disabled",
        "X_ACCESS_TOKEN": "parity-disabled",
        "X_ACCESS_SECRET": "parity-disabled",
        "X_MY_USER_ID": "0",
        "XAI_API_KEY": "parity-disabled",
        "X_BEARER_TOKEN": "parity-disabled",
    }
    os.environ.update(env)
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location(
        "mrs_editorial_parity_bot", root / "mrsMThatcher2.py"
    )
    if spec is None or spec.loader is None:
        raise ParityError(f"could not import bot from {root}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with blocked_network():
        spec.loader.exec_module(module)
    for handler in list(module.log.handlers):
        module.log.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    module.log.addHandler(logging.NullHandler())
    module.log.setLevel(logging.CRITICAL)
    module.log.propagate = False
    return module


def configure_paths(bot: Any, root: Path, runtime: Path) -> None:
    """Point every selector input/read-write path at the requested tree/runtime."""
    bot.LINES_FILE = root / "mrsMThatcher.txt"
    bot.QUOTE_ANALYSIS_FILE = root / "quote_analysis.json"
    bot.QUOTE_ANALYSIS_OVERRIDES_FILE = root / "quote_analysis_overrides.json"
    bot.IMAGE_ANALYSIS_FILE = root / "image_analysis.json"
    bot.GENERATED_IMAGE_ANALYSIS_FILE = str(root / "generated_image_analysis.json")
    bot.IMAGE_GLOB = str(root / "images" / "t*")
    bot.GENERATED_IMAGE_DIR = str(root / "generated_review_approved_images")
    bot.GENERATED_IMAGE_GLOB = "*.png"
    bot.ORIGINAL_EDITORIAL_ANALYSIS_FILE = str(
        root / "original_image_editorial_analysis_experiment_v1.json"
    )
    bot.GENERATED_IDENTITY_AUDIT_FILE = str(
        root / "generated_image_identity_dependence_audit.json"
    )
    bot.IMAGES_USED_FILE = runtime / "images_used.json"
    bot.LINES_USED_FILE = runtime / "lines_used.json"
    bot.STATE_FILE = runtime / "state.json"
    bot.REGULAR_POST_RECEIPT_FILE = runtime / "regular_post_receipt.json"
    bot.MEME_POST_RECEIPT_FILE = runtime / "meme_post_receipt.json"
    bot.CONFIRMED_REPLY_RECEIPT_FILE = runtime / "confirmed_reply_receipt.json"
    bot.LOCK_FILE = runtime / "bot.lock"
    bot.save_image_used_basenames = lambda *_args, **_kwargs: None
    bot.save_quote_used_hashes = lambda *_args, **_kwargs: None
    bot.save_used_set = lambda *_args, **_kwargs: None
    bot.current_datetime = lambda: datetime(
        2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Europe/London")
    )
    bot.now_epoch = lambda: 1_788_453_600
    bot.quote_image_semantic_veto = {"enabled": False, "mode": "disabled"}
    bot.ENABLE_GENERATED_IMAGE_POOL = True
    bot.GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN = 2
    bot.GENERATED_IMAGE_ORIGIN_QUOTE_BOOST = 6
    bot.ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING = False
    bot.ENABLE_GENERATED_IDENTITY_POLICY_SCORING = False
    bot._ORIGINAL_EDITORIAL_ANALYSIS_CACHE = {}
    bot._GENERATED_IDENTITY_AUDIT_CACHE = {}


def configure_mode(bot: Any, mode: str, *, is_base: bool) -> None:
    """Select a legacy base mode or one new canonical mode."""
    bot.ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING = mode == "shadow"
    if is_base:
        return
    resolved = "production" if mode == "rejected" else mode
    bot._ORIGINAL_EDITORIAL_RESOLVED_MODE = resolved
    bot._ORIGINAL_EDITORIAL_MODE_SOURCE = "parity-harness"
    bot._ORIGINAL_EDITORIAL_MODE_RESOLUTION_LOCKED = True
    bot._ORIGINAL_EDITORIAL_PRODUCTION_POLICY = None
    bot._ORIGINAL_EDITORIAL_POLICY_FILE_IDENTITY = None
    bot._ORIGINAL_EDITORIAL_PENDING_DECISION = None
    bot.ORIGINAL_EDITORIAL_CIRCUIT_BREAKER.reset_for_process(
        resolved_mode=resolved, mode_source="parity-harness"
    )
    if mode == "rejected":
        bot.ORIGINAL_EDITORIAL_CIRCUIT_BREAKER.open(
            "policy_unavailable",
            at=datetime(2000, 1, 1, tzinfo=ZoneInfo("UTC")),
        )


def quote_rows(quote_analysis: dict) -> list[dict]:
    """Build stable selector-shaped rows from validated quote metadata."""
    rows: list[dict] = []
    for quote_hash, item in sorted(quote_analysis["items"].items()):
        analysis = item.get("analysis")
        text = item.get("text")
        if not isinstance(analysis, dict) or not isinstance(text, str):
            continue
        rows.append(
            {
                "line_no": int((item.get("line_numbers") or [0])[0]),
                "text": text,
                "quote_hash": quote_hash,
                "analysis": analysis,
                "weight": 1.0,
                "season_status": {
                    "in_window": False,
                    "hard_excluded": False,
                    "relevance": "none",
                },
            }
        )
    if len(rows) < 20:
        raise ParityError("quote analysis has too few usable rows")
    return rows


def compact_rng_state(state: object) -> str:
    """Hash the full RNG state without weakening exact equality."""
    return value_sha256(repr(state))


def compact_candidate(candidate: dict) -> dict:
    """Retain all baseline score evidence needed for exact comparisons."""
    return {
        "basename": candidate.get("basename"),
        "image_hash": candidate.get("image_hash"),
        "source": candidate.get("image_source"),
        "score": candidate.get("score"),
        "components": candidate.get("components"),
        "cycle_reset": candidate.get("cycle_reset"),
        "origin_quote_hash": candidate.get("origin_quote_hash"),
        "origin_quote_match": candidate.get("origin_quote_match"),
        "origin_quote_boost": candidate.get("origin_quote_boost"),
    }


def stable_events(messages: list[str]) -> list[dict]:
    """Decode structured events while retaining their event names."""
    result: list[dict] = []
    for message in messages:
        prefix = next(
            item for item in RELEVANT_EVENT_PREFIXES if message.startswith(item)
        )
        result.append(
            {
                "event": prefix.strip(),
                "payload": json.loads(message[len(prefix) :]),
            }
        )
    return result


def merged_analysis(bot: Any) -> dict:
    """Load and merge the two frozen image-analysis files exactly once."""
    primary = bot.load_image_analysis_file(
        Path(bot.IMAGE_ANALYSIS_FILE), label="image analysis"
    )
    generated = bot.load_image_analysis_file(
        Path(bot.GENERATED_IMAGE_ANALYSIS_FILE), label="generated image analysis"
    )
    if primary is None or generated is None:
        raise ParityError("validated image analysis is unavailable")
    return bot.merge_image_analysis(primary, generated)


def select_quote_for_case(bot: Any, rows: list[dict], index: int, scenario: str) -> dict:
    """Exercise the exact quote tie/weight path while enabling origin matches."""
    if scenario == "origin_quote_match":
        generated = sorted(
            Path(bot.GENERATED_IMAGE_DIR).glob("tg_*.png")
        )
        hashes = {
            path.name[3:-4]: path for path in generated if len(path.name) == 71
        }
        chosen = next((row for row in rows if row["quote_hash"] in hashes), None)
        if chosen is None:
            raise ParityError("no generated origin quote exists in quote analysis")
        candidates = [chosen]
    else:
        start = (index * 7) % (len(rows) - 3)
        candidates = [copy.deepcopy(row) for row in rows[start : start + 3]]
        candidates[0]["weight"] = 0.5
        candidates[1]["weight"] = 1.0
        candidates[2]["weight"] = 2.0
    return bot.select_quote_candidate(candidates)


def candidate_paths_for_case(
    root: Path,
    quote: dict,
    originals: list[Path],
    generated: list[Path],
    index: int,
    scenario: str,
) -> list[Path]:
    """Return a small deterministic phase-specific pool."""
    offset = (index * 5) % (len(originals) - 7)
    original_rows = originals[offset : offset + 6]
    if scenario == "origin_quote_match":
        origin = root / "generated_review_approved_images" / (
            "tg_" + str(quote["quote_hash"]) + ".png"
        )
        if not origin.is_file():
            raise ParityError(f"origin image missing: {origin.name}")
        return [*original_rows[:4], origin]
    generated_offset = (index * 3) % (len(generated) - 3)
    generated_rows = generated[generated_offset : generated_offset + 3]
    if scenario in {
        "normal_mixed",
        "generated_spacing_blocked",
        "identity_policy_enabled",
    }:
        return [*original_rows[:5], *generated_rows[:2]]
    return list(original_rows)


def analysis_for_case(
    base_analysis: dict,
    paths: list[Path],
    quote: dict,
    scenario: str,
) -> dict:
    """Create a bounded copy with deterministic eligibility edge cases."""
    analysis = dict(base_analysis)
    analysis["path_index"] = dict(base_analysis["path_index"])
    analysis["items"] = dict(base_analysis["items"])

    def mutable_image_analysis(path: Path) -> dict:
        image_hash = analysis["path_index"][path.name]
        entry = copy.deepcopy(analysis["items"][image_hash])
        analysis["items"][image_hash] = entry
        return entry["analysis"]

    first_name = paths[0].name
    first_hash = analysis["path_index"][first_name]
    if scenario == "seasonal_exclusion":
        first = mutable_image_analysis(paths[0])
        first["seasonality"] = {
            "avoid_outside_season_or_occasion": True,
            "occasions": ["christmas"],
            "visible_season": "winter",
        }
    elif scenario == "stale_exclusion":
        stale_hash = "f" * 64
        analysis["path_index"][first_name] = stale_hash
        analysis["items"][stale_hash] = copy.deepcopy(
            analysis["items"][first_hash]
        )
    elif scenario in {"visual_mismatch_exclusion", "no_candidate_outcome"}:
        quote_analysis = copy.deepcopy(quote["analysis"])
        preferences = quote_analysis.setdefault("archive_image_preferences", {})
        preferences["strong_visual_mismatches"] = ["parity sentinel"]
        quote["analysis"] = quote_analysis
        targets = paths if scenario == "no_candidate_outcome" else paths[:1]
        for path in targets:
            row = mutable_image_analysis(path)
            row["description"] = str(row.get("description") or "") + " parity sentinel"
    return analysis


def run_case(
    bot: Any,
    root: Path,
    quote_metadata: list[dict],
    base_analysis: dict,
    originals: list[Path],
    generated: list[Path],
    index: int,
    mode: str,
) -> dict:
    """Run one deterministic real-selector case."""
    scenario = SCENARIOS[index % len(SCENARIOS)]
    bot.random.seed(0x4D525350 + index)
    rng_before = compact_rng_state(bot.random.getstate())
    quote = select_quote_for_case(bot, quote_metadata, index, scenario)
    quote_rng_after = compact_rng_state(bot.random.getstate())
    schedule_rng_before = bot.random.getstate()
    schedule_fields, schedule_delay = bot.next_quote_schedule_fields(
        1_788_453_600
    )
    schedule_rng_after = compact_rng_state(bot.random.getstate())
    bot.random.setstate(schedule_rng_before)

    paths = candidate_paths_for_case(
        root, quote, originals, generated, index, scenario
    )
    per_case_analysis = analysis_for_case(base_analysis, paths, quote, scenario)
    bot.load_image_analysis = lambda: per_case_analysis
    bot.current_image_paths = lambda: [str(path) for path in paths]
    bot.ENABLE_GENERATED_IMAGE_POOL = any(
        path.parent.name == "generated_review_approved_images" for path in paths
    )
    bot.ENABLE_GENERATED_IDENTITY_POLICY_SCORING = (
        scenario == "identity_policy_enabled"
    )
    bot.ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING = False

    state = {
        "last_regular_image_filename": paths[-1].name,
        "original_regular_posts_since_generated_image": (
            0 if scenario == "generated_spacing_blocked" else 2
        ),
    }
    images_used: set[str] = set()
    force_reset = scenario in {"forced_cycle_reset", "last_image_fallback"}
    avoid_last = scenario != "last_image_fallback"
    if scenario in {"forced_cycle_reset", "last_image_fallback", "used_cycle_transition"}:
        images_used.update(path.name for path in paths)
    used_before = sorted(images_used)
    state_before = copy.deepcopy(state)

    original_score = bot.score_image_for_quote
    if scenario == "equal_baseline_tie":
        bot.score_image_for_quote = lambda *_args, **_kwargs: (
            7.25,
            {"parity_equal_tie": 7.25},
            True,
        )

    capture: dict[str, Any] = {}
    events = EventCapture()
    bot.log.addHandler(events)
    original_editorial_log = bot.log_original_editorial_shadow_result

    def editorial_capture(
        selected_quote: dict,
        baseline: dict,
        scored: list[dict],
        *,
        selection_phase: str,
    ) -> None:
        capture["baseline"] = baseline
        capture["candidates"] = scored
        capture["selection_phase"] = selection_phase
        original_editorial_log(
            selected_quote,
            baseline,
            scored,
            selection_phase=selection_phase,
        )

    bot.log_original_editorial_shadow_result = editorial_capture
    exception: dict[str, str] | None = None
    selected: dict | None = None
    try:
        selected = bot.choose_matched_unused_image(
            images_used,
            quote,
            state,
            force_cycle_reset=force_reset,
            avoid_last_image_at_cycle_boundary=avoid_last,
            cycle_boundary_exclusions=(set() if force_reset else None),
            generated_images_allowed=bot.generated_images_allowed_by_spacing(state),
            selection_phase=(
                "last_image_fallback"
                if scenario == "last_image_fallback"
                else "forced_cycle_reset"
                if scenario == "forced_cycle_reset"
                else "normal"
            ),
        )
    except Exception as exc:  # expected no-candidate outcomes are parity data
        exception = {
            "type": type(exc).__name__,
            "message": str(exc).replace(str(root), "<REPOSITORY>"),
        }
    finally:
        bot.log_original_editorial_shadow_result = original_editorial_log
        bot.score_image_for_quote = original_score
        bot.log.removeHandler(events)

    candidates = capture.get("candidates") or []
    baseline = capture.get("baseline")
    observation = {
        "index": index,
        "scenario": scenario,
        "quote_hash": quote["quote_hash"],
        "quote_rng_before": rng_before,
        "quote_rng_after": quote_rng_after,
        "schedule": {
            "fields": schedule_fields,
            "delay": schedule_delay,
            "rng_before": compact_rng_state(schedule_rng_before),
            "rng_after": schedule_rng_after,
        },
        "selection_rng_after": compact_rng_state(bot.random.getstate()),
        "candidate_order": [row.get("basename") for row in candidates],
        "candidates": [compact_candidate(row) for row in candidates],
        "baseline": compact_candidate(baseline) if baseline else None,
        "selected": compact_candidate(selected) if selected else None,
        "selected_is_supplied_object": bool(
            selected is not None and any(selected is row for row in candidates)
        ),
        "selection_phase": capture.get("selection_phase"),
        "images_used_before": used_before,
        "images_used_after": sorted(images_used),
        "state_before": state_before,
        "state_after": state,
        "generated_spacing_allowed": bot.generated_images_allowed_by_spacing(state),
        "generated_identity_policy_enabled": bool(
            bot.ENABLE_GENERATED_IDENTITY_POLICY_SCORING
        ),
        "events": stable_events(events.messages),
        "exception": exception,
    }
    if mode == "rejected" and hasattr(bot, "ORIGINAL_EDITORIAL_CIRCUIT_BREAKER"):
        observation["editorial_breaker_open"] = bool(
            bot.ORIGINAL_EDITORIAL_CIRCUIT_BREAKER.is_open
        )
    return observation


def worker(args: argparse.Namespace) -> int:
    """Run one implementation/mode and emit exact per-case observations."""
    root = Path(args.root).resolve()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with blocked_network(), tempfile.TemporaryDirectory(
        prefix="mrs-parity-worker-", dir=output.parent
    ) as temporary:
        runtime = Path(temporary)
        bot = import_bot(root, runtime)
        configure_paths(bot, root, runtime)
        configure_mode(bot, args.mode, is_base=args.is_base)
        quote_analysis = bot.load_quote_analysis()
        if not isinstance(quote_analysis, dict):
            raise ParityError("quote analysis failed validation")
        quotes = quote_rows(quote_analysis)
        images = merged_analysis(bot)
        # Prime strict metadata caches against the complete pool.  Individual
        # cases subsequently narrow only the final phase-specific candidates.
        # That mirrors production, where metadata validation is corpus-wide
        # and eligibility filtering happens afterwards.
        bot.load_original_editorial_analysis()
        bot.load_generated_identity_audit()
        originals = sorted((root / "images").glob("t*"))
        generated = sorted(
            (root / "generated_review_approved_images").glob("*.png")
        )
        if len(originals) < 10 or len(generated) < 5:
            raise ParityError("candidate corpus is incomplete")
        image_hash_cache = {
            str(path.resolve()): file_sha256(path)
            for path in (*originals, *generated)
        }
        bot.current_image_sha256 = lambda path: image_hash_cache[
            str(Path(path).resolve())
        ]
        records = [
            run_case(
                bot,
                root,
                quotes,
                images,
                originals,
                generated,
                index,
                args.mode,
            )
            for index in range(args.count)
        ]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "harness_version": HARNESS_VERSION,
        "root": str(root),
        "mode": args.mode,
        "is_base": bool(args.is_base),
        "count": len(records),
        "scenario_counts": dict(sorted(Counter(row["scenario"] for row in records).items())),
        "records": records,
    }
    output.write_bytes(canonical_json(payload) + b"\n")
    return 0


def run_worker(
    script: Path,
    root: Path,
    mode: str,
    count: int,
    output: Path,
    *,
    is_base: bool,
) -> dict:
    """Run and read one isolated worker."""
    command = [
        sys.executable,
        str(script),
        "--worker",
        "--root",
        str(root),
        "--mode",
        mode,
        "--count",
        str(count),
        "--output",
        str(output),
    ]
    if is_base:
        command.append("--is-base")
    subprocess.run(
        command,
        check=True,
        env={**os.environ, "MRS_TEST_MODE": "1"},
    )
    return json.loads(output.read_text(encoding="utf-8"))


def comparison_view(record: dict, *, include_shadow: bool) -> dict:
    """Exclude only new-policy state that has no base-commit representation."""
    result = copy.deepcopy(record)
    result.pop("editorial_breaker_open", None)
    if not include_shadow:
        result["events"] = [
            event
            for event in result.get("events", [])
            if event.get("event") != "ORIGINAL_EDITORIAL_SHADOW_RESULT"
        ]
    return result


def compare_pair(
    expected: dict,
    actual: dict,
    *,
    name: str,
    include_shadow: bool,
) -> dict:
    """Compare all observations exactly and retain bounded diagnostics."""
    if expected["count"] != actual["count"]:
        raise ParityError(f"{name}: worker case counts differ")
    mismatches: list[dict] = []
    for expected_row, actual_row in zip(expected["records"], actual["records"]):
        expected_view = comparison_view(expected_row, include_shadow=include_shadow)
        actual_view = comparison_view(actual_row, include_shadow=include_shadow)
        if expected_view != actual_view and len(mismatches) < 5:
            mismatches.append(
                {
                    "index": expected_row["index"],
                    "scenario": expected_row["scenario"],
                    "expected_sha256": value_sha256(expected_view),
                    "actual_sha256": value_sha256(actual_view),
                    "expected": expected_view,
                    "actual": actual_view,
                }
            )
    mismatch_count = sum(
        comparison_view(expected_row, include_shadow=include_shadow)
        != comparison_view(actual_row, include_shadow=include_shadow)
        for expected_row, actual_row in zip(expected["records"], actual["records"])
    )
    return {
        "comparison": name,
        "count": expected["count"],
        "exact_match_count": expected["count"] - mismatch_count,
        "mismatch_count": mismatch_count,
        "passed": mismatch_count == 0,
        "bounded_mismatch_examples": mismatches,
    }


def controller(args: argparse.Namespace) -> int:
    """Run all four workers and write a compact durable report."""
    base_root = Path(args.base_root).resolve()
    candidate_root = Path(args.candidate_root).resolve()
    output = Path(args.output).resolve()
    if not re.fullmatch(r"[0-9a-f]{40}", str(args.base_commit)):
        raise ParityError("--base-commit must be a full lowercase Git SHA")
    try:
        observed_base_commit = subprocess.run(
            ["git", "-C", str(base_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ParityError(
            "--base-root must be a readable Git worktree at the verified base commit"
        ) from exc
    if observed_base_commit != args.base_commit:
        raise ParityError(
            "base worktree HEAD does not match --base-commit: "
            f"{observed_base_commit} != {args.base_commit}"
        )
    observed_base_changes = subprocess.run(
        [
            "git",
            "-C",
            str(base_root),
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    ).stdout.strip()
    if observed_base_changes:
        raise ParityError("base worktree has tracked changes and is not an exact baseline")
    output.parent.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve()
    with tempfile.TemporaryDirectory(
        prefix="mrs-editorial-parity-", dir=output.parent
    ) as temporary:
        run_dir = Path(temporary)
        base_disabled = run_worker(
            script,
            base_root,
            "disabled",
            args.count,
            run_dir / "base-disabled.json",
            is_base=True,
        )
        base_shadow = run_worker(
            script,
            base_root,
            "shadow",
            args.count,
            run_dir / "base-shadow.json",
            is_base=True,
        )
        candidate_disabled = run_worker(
            script,
            candidate_root,
            "disabled",
            args.count,
            run_dir / "candidate-disabled.json",
            is_base=False,
        )
        candidate_shadow = run_worker(
            script,
            candidate_root,
            "shadow",
            args.count,
            run_dir / "candidate-shadow.json",
            is_base=False,
        )
        candidate_rejected = run_worker(
            script,
            candidate_root,
            "rejected",
            args.count,
            run_dir / "candidate-rejected.json",
            is_base=False,
        )
        comparisons = [
            compare_pair(
                base_disabled,
                candidate_disabled,
                name="base_vs_disabled",
                include_shadow=False,
            ),
            compare_pair(
                base_shadow,
                candidate_shadow,
                name="base_vs_shadow",
                include_shadow=True,
            ),
            compare_pair(
                base_disabled,
                candidate_rejected,
                name="base_vs_production_all_rejected",
                include_shadow=False,
            ),
        ]
    report = {
        "schema_version": SCHEMA_VERSION,
        "harness_version": HARNESS_VERSION,
        "base_root": str(base_root),
        "candidate_root": str(candidate_root),
        "base_commit": observed_base_commit,
        "selection_count_per_comparison": args.count,
        "total_exact_comparisons": args.count * len(comparisons),
        "scenario_counts": base_disabled["scenario_counts"],
        "comparisons": comparisons,
        "passed": all(item["passed"] for item in comparisons),
        "network_calls": 0,
        "production_writes": 0,
    }
    output.write_bytes(canonical_json(report) + b"\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


def parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    result = argparse.ArgumentParser(
        description="Exact base-commit parity harness for original editorial modes."
    )
    result.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    result.add_argument("--root")
    result.add_argument("--mode", choices=("disabled", "shadow", "rejected"))
    result.add_argument("--is-base", action="store_true", help=argparse.SUPPRESS)
    result.add_argument("--count", type=int, default=DEFAULT_COUNT)
    result.add_argument("--output", required=True)
    result.add_argument("--base-root")
    result.add_argument("--candidate-root", default=str(ROOT))
    result.add_argument(
        "--base-commit",
        default="b32a0be4678364311a8d25385ccaa3a1f035d7c5",
    )
    return result


def main() -> int:
    """Run a worker or the controller."""
    args = parser().parse_args()
    if args.count <= 0:
        raise SystemExit("--count must be positive")
    if args.worker:
        if not args.root or not args.mode:
            raise SystemExit("worker mode requires --root and --mode")
        return worker(args)
    if not args.base_root:
        raise SystemExit("controller mode requires --base-root")
    return controller(args)


if __name__ == "__main__":
    raise SystemExit(main())

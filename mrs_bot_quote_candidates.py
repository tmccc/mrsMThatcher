"""Ordinary quotation eligibility, candidate preparation and weighted selection.

Explicit calls read the supplied source, analysis and canonical eligibility
manifest, and may clear the caller's in-memory used set at cycle exhaustion.
The coordinator supplies current configuration, helpers and logger; durable
history, image selection, publishing and research implementation stay outside.
Importing this module does no runtime work and retains no callbacks or state.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from datetime import datetime
from logging import Logger
from pathlib import Path


def mm_dd_in_window(mm_dd: str, start_mm_dd: str, end_mm_dd: str) -> bool:
    """Return the mm dd in window."""
    if start_mm_dd <= end_mm_dd:
        return start_mm_dd <= mm_dd <= end_mm_dd
    return mm_dd >= start_mm_dd or mm_dd <= end_mm_dd


def any_window_matches_today(
    windows: object,
    today_mm_dd: str,
    *,
    mm_dd_in_window: Callable[[str, str, str], bool],
) -> bool:
    """Return whether any window matches today."""
    if not isinstance(windows, list):
        return False
    for window in windows:
        if not isinstance(window, dict):
            continue
        start = str(window.get("start_mm_dd", "") or "")
        end = str(window.get("end_mm_dd", "") or "")
        if re.fullmatch(r"\d{2}-\d{2}", start) and re.fullmatch(r"\d{2}-\d{2}", end):
            if mm_dd_in_window(today_mm_dd, start, end):
                return True
    return False


def quote_season_status(
    analysis: dict | None,
    *,
    today_mm_dd: str,
    any_window_matches_today: Callable[[object, str], bool],
) -> dict:
    """Return the quote season status."""
    seasonality = (analysis or {}).get("seasonality", {}) if isinstance(analysis, dict) else {}
    if not isinstance(seasonality, dict):
        seasonality = {}
    windows = seasonality.get("preferred_windows", [])
    in_window = any_window_matches_today(windows, today_mm_dd)
    hard_excluded = bool(seasonality.get("hard_exclude_outside_windows")) and bool(windows) and not in_window
    relevance = str(seasonality.get("relevance", "none") or "none")
    return {
        "in_window": in_window,
        "hard_excluded": hard_excluded,
        "relevance": relevance,
    }


def quote_candidate_weight(
    analysis: dict | None,
    *,
    today_mm_dd: str,
    quote_season_status: Callable[..., dict],
    season_date_specific_weight: float,
    season_strong_weight: float,
    season_soft_weight: float,
    quality_weight_max_multiplier: float,
) -> tuple[float, dict]:
    """Return the quote candidate weight."""
    status = quote_season_status(analysis, today_mm_dd=today_mm_dd)
    if status["hard_excluded"]:
        return 0.0, status

    weight = 1.0
    if status["in_window"]:
        if status["relevance"] == "date_specific":
            weight *= season_date_specific_weight
        elif status["relevance"] == "strong":
            weight *= season_strong_weight
        elif status["relevance"] == "soft":
            weight *= season_soft_weight

    scores = (analysis or {}).get("scores", {}) if isinstance(analysis, dict) else {}
    if isinstance(scores, dict):
        values = []
        for key in ("general_post_suitability", "standalone_clarity", "visual_matchability"):
            try:
                values.append(max(0.0, min(100.0, float(scores.get(key, 50)))))
            except Exception:
                pass
        if values:
            quality = sum(values) / len(values)
            weight *= 1.0 + ((quality - 50.0) / 50.0) * (quality_weight_max_multiplier - 1.0)
            weight = max(0.2, weight)

    return weight, status


def weighted_random_choice(candidates: list[dict]) -> dict:
    """Select one candidate using its configured weight."""
    total = sum(float(candidate.get("weight", 0.0)) for candidate in candidates)
    if total <= 0:
        return random.choice(candidates)
    target = random.uniform(0.0, total)
    running = 0.0
    for candidate in candidates:
        running += float(candidate.get("weight", 0.0))
        if running >= target:
            return candidate
    return candidates[-1]


def current_quote_hashes_by_line(
    lines: list[str],
    *,
    quote_text_hash: Callable[[str], str],
) -> dict[int, str]:
    """Return whether current quote hashes by line."""
    result: dict[int, str] = {}
    for line_no, line in enumerate(lines):
        if line.rstrip():
            result[line_no] = quote_text_hash(line)
    return result


def build_quote_candidates(
    lines: list[str],
    available_lines: list[int],
    quote_analysis: dict | None,
    today_mm_dd: str,
    *,
    excluded_quote_hashes: set[str] | None = None,
    quote_text_hash: Callable[[str], str],
    quote_metadata_for_hash: Callable[..., dict | None],
    quote_candidate_weight: Callable[..., tuple[float, dict]],
    log: Logger,
) -> tuple[list[dict], int, int]:
    """Build analysed, research-eligible quotation candidates for a date."""
    hard_excluded = 0
    non_empty = 0
    candidates: list[dict] = []
    excluded_quote_hashes = excluded_quote_hashes or set()
    seen_hashes: set[str] = set()

    for line_no in available_lines:
        tweet = lines[line_no].rstrip()
        if not tweet:
            log.debug("Skipping empty line_no=%d", line_no)
            continue
        quote_hash = quote_text_hash(tweet)
        if quote_hash in seen_hashes:
            log.debug("Skipping duplicate quote line_no=%d quote_hash=%s", line_no, quote_hash)
            continue
        seen_hashes.add(quote_hash)
        if quote_hash in excluded_quote_hashes:
            continue
        non_empty += 1

        analysis = quote_metadata_for_hash(quote_analysis, quote_hash, tweet)
        if analysis is None:
            log.warning("Skipping unanalysed current quote line_no=%d quote_hash=%s until quote analysis is refreshed", line_no, quote_hash)
            continue
        weight, season_status = quote_candidate_weight(analysis, today_mm_dd=today_mm_dd)
        if weight <= 0:
            hard_excluded += 1
            continue
        candidates.append(
            {
                "line_no": line_no,
                "text": tweet,
                "quote_hash": quote_hash,
                "analysis": analysis,
                "weight": weight,
                "season_status": season_status,
            }
        )
    return candidates, hard_excluded, non_empty


def load_quote_lines_and_analysis(
    *,
    lines_file: Path,
    load_quote_analysis: Callable[[], dict | None],
    validate_quote_analysis_against_lines: Callable[[dict | None, list[str]], None],
    current_datetime: Callable[[], datetime],
    log: Logger,
) -> tuple[list[str], dict | None, str]:
    """Load the active quotation source and validated analysis metadata."""
    with open(lines_file) as f:
        lines = f.readlines()

    log.debug("Loaded %d lines from %s", len(lines), lines_file)

    if not lines:
        raise RuntimeError(f"No lines found in {lines_file}")

    quote_analysis = load_quote_analysis()
    if quote_analysis is None:
        raise RuntimeError(f"Quote analysis unavailable or invalid; refusing regular quote posting from {lines_file}")
    validate_quote_analysis_against_lines(quote_analysis, lines)
    return lines, quote_analysis, current_datetime().strftime("%m-%d")


def load_completed_research_quote_hashes(
    *,
    historical_context_research_dir: Path,
    runtime_eligible_quote_manifest_file: Path,
    lines_file: Path,
    completed_quote_research_file: Path,
    load_json_object: Callable[..., dict | None],
    file_sha256: Callable[[Path], str],
    quote_text_hash: Callable[[str], str],
) -> set[str]:
    """Load the exact hash-bound ordinary-post eligibility partition."""
    from historical_context_formatter import (
        THATCHER_ATTRIBUTION_RULE_VERSION,
        load_and_validate_corpus_core,
        packet_is_attributed_to_margaret_thatcher,
    )

    packets, _unresolved = load_and_validate_corpus_core(
        historical_context_research_dir
    )
    manifest = load_json_object(
        runtime_eligible_quote_manifest_file,
        label="runtime eligible quotation manifest",
    )
    if manifest is None:
        raise RuntimeError(
            "Runtime eligible quotation manifest unavailable; refusing regular "
            f"quote posting: {runtime_eligible_quote_manifest_file}"
        )

    runtime_ids = manifest.get("runtime_eligible_quote_ids")
    resolved_ids = manifest.get("resolved_manifest_quote_ids")
    aliases = manifest.get("runtime_quote_aliases")
    source_hashes = manifest.get("source_file_hashes")
    declared_count = manifest.get("runtime_eligible_quote_count")
    declared_source_count = manifest.get("source_record_count")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("eligibility_rule_version")
        != THATCHER_ATTRIBUTION_RULE_VERSION
        or type(declared_count) is not int
        or type(declared_source_count) is not int
        or not isinstance(runtime_ids, list)
        or not isinstance(resolved_ids, list)
        or not isinstance(aliases, dict)
        or not isinstance(source_hashes, dict)
        or any(
            not isinstance(value, str)
            or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in runtime_ids
        )
        or any(
            not isinstance(value, str)
            or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in resolved_ids
        )
        or any(
            not isinstance(key, str)
            or re.fullmatch(r"[0-9a-f]{64}", key) is None
            or not isinstance(value, str)
            or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for key, value in aliases.items()
        )
    ):
        raise RuntimeError(
            "Runtime eligible quotation manifest structure is invalid; refusing "
            "regular quote posting"
        )
    if (
        declared_count <= 0
        or len(runtime_ids) != declared_count
        or len(set(runtime_ids)) != declared_count
        or len(resolved_ids) != declared_count
        or len(set(resolved_ids)) != declared_count
        or not set(aliases).issubset(set(runtime_ids))
        or sorted(aliases.get(value, value) for value in runtime_ids)
        != resolved_ids
    ):
        raise RuntimeError(
            "Runtime eligible quotation manifest counts or aliases are invalid; "
            "refusing regular quote posting"
        )

    current_source_hash = file_sha256(lines_file)
    current_packets_hash = file_sha256(completed_quote_research_file)
    if (
        source_hashes.get("active_source") != current_source_hash
        or source_hashes.get("completed_quote_research")
        != current_packets_hash
    ):
        raise RuntimeError(
            "Runtime eligible quotation manifest source hashes are stale; "
            "refusing regular quote posting"
        )

    with lines_file.open("r", encoding="utf-8") as handle:
        current_source_ids = {
            quote_text_hash(line.rstrip("\n"))
            for line in handle
            if line.strip()
        }
    if (
        declared_source_count != len(current_source_ids)
        or not set(runtime_ids).issubset(current_source_ids)
    ):
        raise RuntimeError(
            "Runtime eligible quotation manifest does not match the active "
            "quotation source"
        )

    eligible_packet_ids = {
        str(quote_id)
        for quote_id, packet in packets.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }
    derived_runtime_ids = {
        quote_text_hash(str(packet.get("quote_text") or ""))
        for packet in packets.values()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }
    if (
        set(resolved_ids) != eligible_packet_ids
        or set(runtime_ids) != derived_runtime_ids
    ):
        raise RuntimeError(
            "Runtime eligible quotation manifest differs from the validated "
            "canonical attribution partition"
        )
    return derived_runtime_ids


def completed_research_quote_hashes(
    *,
    load_completed_research_quote_hashes: Callable[[], set[str]],
) -> set[str]:
    """Return current attribution-eligible completed quotation hashes."""
    return load_completed_research_quote_hashes()


def quote_candidates_for_current_cycle(
    lines_used: set,
    *,
    excluded_quote_hashes: set[str] | None = None,
    load_quote_lines_and_analysis: Callable[[], tuple[list[str], dict | None, str]],
    current_quote_hashes_by_line: Callable[[list[str]], dict[int, str]],
    completed_research_quote_hashes: Callable[[], set[str]],
    build_quote_candidates: Callable[..., tuple[list[dict], int, int]],
    lines_file: Path,
    log: Logger,
) -> list[dict]:
    """Build the unused runtime-eligible quotation pool for the current cycle."""
    log.debug("Choosing unused line. Already used=%d", len(lines_used))

    lines, quote_analysis, today_mm_dd = load_quote_lines_and_analysis()
    hashes_by_line = current_quote_hashes_by_line(lines)
    completed_hashes = completed_research_quote_hashes()
    research_ineligible_hashes = set(hashes_by_line.values()).difference(completed_hashes)
    excluded_quote_hashes = set(excluded_quote_hashes or set()).union(research_ineligible_hashes)
    if research_ineligible_hashes:
        log.info(
            "Excluded %d source quotation(s) without attribution-eligible completed canonical research packets",
            len(research_ineligible_hashes),
        )
    unused_research_eligible_lines = [
        line_no
        for line_no, quote_hash in hashes_by_line.items()
        if quote_hash not in lines_used and quote_hash not in excluded_quote_hashes
    ]
    available_lines = [line_no for line_no, quote_hash in hashes_by_line.items() if quote_hash not in lines_used]

    log.debug("Available unused lines=%d", len(available_lines))

    if not unused_research_eligible_lines:
        log.info("All attribution-eligible researched quotations used; clearing line history")
        lines_used.clear()
        available_lines = list(hashes_by_line)

    candidates, hard_excluded, non_empty = build_quote_candidates(
        lines,
        available_lines,
        quote_analysis,
        today_mm_dd,
        excluded_quote_hashes=excluded_quote_hashes,
    )

    log.info(
        "Quote candidate pool: unused_non_empty=%d hard_excluded_by_date=%d",
        len(candidates),
        hard_excluded,
    )

    if not candidates and non_empty > 0 and hard_excluded == non_empty:
        log.warning(
            "Quote cycle is seasonally exhausted: %d unused quote(s) are hard-excluded today; resetting quote cycle",
            hard_excluded,
        )
        lines_used.clear()
        available_lines = list(hashes_by_line)
        candidates, hard_excluded, non_empty = build_quote_candidates(
            lines,
            available_lines,
            quote_analysis,
            today_mm_dd,
            excluded_quote_hashes=excluded_quote_hashes,
        )
        log.info(
            "Quote candidate pool after seasonal reset: unused_non_empty=%d hard_excluded_by_date=%d",
            len(candidates),
            hard_excluded,
        )

    if not candidates and non_empty > 0:
        full_candidates, full_hard_excluded, full_non_empty = build_quote_candidates(
            lines,
            list(hashes_by_line),
            quote_analysis,
            today_mm_dd,
            excluded_quote_hashes=excluded_quote_hashes,
        )
        if full_candidates:
            log.warning(
                "Quote cycle is exhausted by currently nonselectable quote(s); resetting quote cycle. "
                "unused_non_empty=%d full_selectable=%d full_hard_excluded=%d",
                non_empty,
                len(full_candidates),
                full_hard_excluded,
            )
            lines_used.clear()
            candidates = full_candidates
        elif full_non_empty:
            log.warning(
                "No currently selectable analysed quote exists in full corpus. non_empty=%d hard_excluded=%d",
                full_non_empty,
                full_hard_excluded,
            )

    if candidates:
        return candidates
    raise RuntimeError(f"No non-empty lines found in {lines_file}")


def select_quote_candidate(
    candidates: list[dict],
    *,
    weighted_random_choice: Callable[[list[dict]], dict],
    log: Logger,
) -> dict:
    """Select quote candidate."""
    chosen = weighted_random_choice(candidates)
    log.info(
        "Selected quote line_no=%d quote_hash=%s weight=%.2f seasonal_boost=%s",
        chosen["line_no"],
        chosen.get("quote_hash"),
        chosen.get("weight", 0.0),
        bool(chosen.get("season_status", {}).get("in_window")),
    )
    log.debug("Selected quote text=%r", chosen["text"])
    return chosen


def choose_unused_line_candidate(
    lines_used: set,
    *,
    excluded_quote_hashes: set[str] | None = None,
    quote_candidates_for_current_cycle: Callable[..., list[dict]],
    select_quote_candidate: Callable[[list[dict]], dict],
) -> dict:
    """Select unused line candidate."""
    return select_quote_candidate(quote_candidates_for_current_cycle(lines_used, excluded_quote_hashes=excluded_quote_hashes))

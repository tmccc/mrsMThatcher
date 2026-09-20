"""Ordinary quotation eligibility, candidate preparation and weighted selection.

Explicit calls read the supplied source, analysis and canonical eligibility
manifest, and may clear the caller's in-memory used set at cycle exhaustion.
The coordinator supplies current configuration, metadata owner and logger; durable
history, image selection, publishing and research implementation stay outside.
Canonical research-loader hashes come directly from the asset-metadata owner.
Candidate hashing remains supplied for isolated simulation caches.
QuoteCandidates binds current external inputs per root call and invokes its own
operations and the supplied AssetMetadata owner directly. Import and construction
perform no runtime work or state access. The public research loader remains
shared with preparation tools.
"""

from __future__ import annotations

from dataclasses import dataclass

import random
import re
from collections.abc import Callable
from datetime import datetime
from logging import Logger
from pathlib import Path

from mrs_bot_asset_metadata import AssetMetadata, quote_text_hash


def mm_dd_in_window(mm_dd: str, start_mm_dd: str, end_mm_dd: str) -> bool:
    """Return the mm dd in window."""
    if start_mm_dd <= end_mm_dd:
        return start_mm_dd <= mm_dd <= end_mm_dd
    return mm_dd >= start_mm_dd or mm_dd <= end_mm_dd


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


def load_completed_research_quote_hashes(
    *,
    historical_context_research_dir: Path,
    runtime_eligible_quote_manifest_file: Path,
    lines_file: Path,
    completed_quote_research_file: Path,
    load_json_object: Callable[..., dict | None],
    file_sha256: Callable[[Path], str],
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


@dataclass(frozen=True)
class QuoteCandidates:
    """Own quotation eligibility and selection through one AssetMetadata owner."""

    season_date_specific_weight: float
    season_strong_weight: float
    season_soft_weight: float
    quality_weight_max_multiplier: float
    quote_text_hash: Callable[[str], str]
    metadata: AssetMetadata
    log: Logger
    lines_file: Path
    current_datetime: Callable[[], datetime]
    research_dir: Path
    eligible_manifest_file: Path
    research_file: Path
    file_sha256: Callable[[Path], str]

    def any_window(
        self,
        windows: object,
        today_mm_dd: str,
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

    def season_status(
        self,
        analysis: dict | None,
        *,
        today_mm_dd: str,
    ) -> dict:
        """Return the quote season status."""
        seasonality = (analysis or {}).get("seasonality", {}) if isinstance(analysis, dict) else {}
        if not isinstance(seasonality, dict):
            seasonality = {}
        windows = seasonality.get("preferred_windows", [])
        in_window = self.any_window(windows, today_mm_dd)
        hard_excluded = bool(seasonality.get("hard_exclude_outside_windows")) and bool(windows) and not in_window
        relevance = str(seasonality.get("relevance", "none") or "none")
        return {
            "in_window": in_window,
            "hard_excluded": hard_excluded,
            "relevance": relevance,
        }

    def weight(
        self,
        analysis: dict | None,
        *,
        today_mm_dd: str,
    ) -> tuple[float, dict]:
        """Return the quote candidate weight."""
        status = self.season_status(analysis, today_mm_dd=today_mm_dd)
        if status["hard_excluded"]:
            return 0.0, status

        weight = 1.0
        if status["in_window"]:
            if status["relevance"] == "date_specific":
                weight *= self.season_date_specific_weight
            elif status["relevance"] == "strong":
                weight *= self.season_strong_weight
            elif status["relevance"] == "soft":
                weight *= self.season_soft_weight

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
                weight *= 1.0 + ((quality - 50.0) / 50.0) * (self.quality_weight_max_multiplier - 1.0)
                weight = max(0.2, weight)

        return weight, status

    def hashes_by_line(
        self,
        lines: list[str],
    ) -> dict[int, str]:
        """Return whether current quote hashes by line."""
        result: dict[int, str] = {}
        for line_no, line in enumerate(lines):
            if line.rstrip():
                result[line_no] = self.quote_text_hash(line)
        return result

    def build(
        self,
        lines: list[str],
        available_lines: list[int],
        quote_analysis: dict | None,
        today_mm_dd: str,
        *,
        excluded_quote_hashes: set[str] | None = None,
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
                self.log.debug("Skipping empty line_no=%d", line_no)
                continue
            quote_hash = self.quote_text_hash(tweet)
            if quote_hash in seen_hashes:
                self.log.debug("Skipping duplicate quote line_no=%d quote_hash=%s", line_no, quote_hash)
                continue
            seen_hashes.add(quote_hash)
            if quote_hash in excluded_quote_hashes:
                continue
            non_empty += 1

            analysis = self.metadata.quote_for_hash(quote_analysis, quote_hash, tweet)
            if analysis is None:
                self.log.warning("Skipping unanalysed current quote line_no=%d quote_hash=%s until quote analysis is refreshed", line_no, quote_hash)
                continue
            weight, season_status = self.weight(analysis, today_mm_dd=today_mm_dd)
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

    def load_source(
        self,
    ) -> tuple[list[str], dict | None, str]:
        """Load the active quotation source and validated analysis metadata."""
        with open(self.lines_file) as f:
            lines = f.readlines()

        self.log.debug("Loaded %d lines from %s", len(lines), self.lines_file)

        if not lines:
            raise RuntimeError(f"No lines found in {self.lines_file}")

        quote_analysis = self.metadata.load_quote()
        if quote_analysis is None:
            raise RuntimeError(f"Quote analysis unavailable or invalid; refusing regular quote posting from {self.lines_file}")
        self.metadata.validate_quote_lines(quote_analysis, lines)
        return lines, quote_analysis, self.current_datetime().strftime("%m-%d")

    def completed(
        self,
    ) -> set[str]:
        """Read the shared hash-bound eligibility partition with current source inputs."""
        return load_completed_research_quote_hashes(
            historical_context_research_dir=self.research_dir,
            runtime_eligible_quote_manifest_file=self.eligible_manifest_file,
            lines_file=self.lines_file,
            completed_quote_research_file=self.research_file,
            load_json_object=self.metadata.load_json,
            file_sha256=self.file_sha256,
        )

    def for_cycle(
        self,
        lines_used: set,
        *,
        excluded_quote_hashes: set[str] | None = None,
        allow_cycle_reset: bool = True,
    ) -> list[dict]:
        """Build unused candidates; disable cycle resets during image-pair retries."""
        self.log.debug("Choosing unused line. Already used=%d", len(lines_used))

        lines, quote_analysis, today_mm_dd = self.load_source()
        hashes_by_line = self.hashes_by_line(lines)
        completed_hashes = self.completed()
        research_ineligible_hashes = set(hashes_by_line.values()).difference(completed_hashes)
        excluded_quote_hashes = set(excluded_quote_hashes or set()).union(research_ineligible_hashes)
        if research_ineligible_hashes:
            self.log.info(
                "Excluded %d source quotation(s) without attribution-eligible completed canonical research packets",
                len(research_ineligible_hashes),
            )
        unused_research_eligible_lines = [
            line_no
            for line_no, quote_hash in hashes_by_line.items()
            if quote_hash not in lines_used and quote_hash not in excluded_quote_hashes
        ]
        available_lines = [line_no for line_no, quote_hash in hashes_by_line.items() if quote_hash not in lines_used]

        self.log.debug("Available unused lines=%d", len(available_lines))

        if allow_cycle_reset and not unused_research_eligible_lines:
            self.log.info("All attribution-eligible researched quotations used; clearing line history")
            lines_used.clear()
            available_lines = list(hashes_by_line)

        candidates, hard_excluded, non_empty = self.build(
            lines,
            available_lines,
            quote_analysis,
            today_mm_dd,
            excluded_quote_hashes=excluded_quote_hashes,
        )

        self.log.info(
            "Quote candidate pool: unused_non_empty=%d hard_excluded_by_date=%d",
            len(candidates),
            hard_excluded,
        )

        if allow_cycle_reset and not candidates and non_empty > 0 and hard_excluded == non_empty:
            self.log.warning(
                "Quote cycle is seasonally exhausted: %d unused quote(s) are hard-excluded today; resetting quote cycle",
                hard_excluded,
            )
            lines_used.clear()
            available_lines = list(hashes_by_line)
            candidates, hard_excluded, non_empty = self.build(
                lines,
                available_lines,
                quote_analysis,
                today_mm_dd,
                excluded_quote_hashes=excluded_quote_hashes,
            )
            self.log.info(
                "Quote candidate pool after seasonal reset: unused_non_empty=%d hard_excluded_by_date=%d",
                len(candidates),
                hard_excluded,
            )

        if allow_cycle_reset and not candidates and non_empty > 0:
            full_candidates, full_hard_excluded, full_non_empty = self.build(
                lines,
                list(hashes_by_line),
                quote_analysis,
                today_mm_dd,
                excluded_quote_hashes=excluded_quote_hashes,
            )
            if full_candidates:
                self.log.warning(
                    "Quote cycle is exhausted by currently nonselectable quote(s); resetting quote cycle. "
                    "unused_non_empty=%d full_selectable=%d full_hard_excluded=%d",
                    non_empty,
                    len(full_candidates),
                    full_hard_excluded,
                )
                lines_used.clear()
                candidates = full_candidates
            elif full_non_empty:
                self.log.warning(
                    "No currently selectable analysed quote exists in full corpus. non_empty=%d hard_excluded=%d",
                    full_non_empty,
                    full_hard_excluded,
                )

        if candidates:
            return candidates
        raise RuntimeError(f"No non-empty lines found in {self.lines_file}")

    def select(
        self,
        candidates: list[dict],
    ) -> dict:
        """Select quote candidate."""
        chosen = weighted_random_choice(candidates)
        self.log.info(
            "Selected quote line_no=%d quote_hash=%s weight=%.2f seasonal_boost=%s",
            chosen["line_no"],
            chosen.get("quote_hash"),
            chosen.get("weight", 0.0),
            bool(chosen.get("season_status", {}).get("in_window")),
        )
        self.log.debug("Selected quote text=%r", chosen["text"])
        return chosen

    def choose(
        self,
        lines_used: set,
        *,
        excluded_quote_hashes: set[str] | None = None,
        allow_cycle_reset: bool = True,
    ) -> dict:
        """Select an unused quotation, optionally preserving history during retries."""
        return self.select(self.for_cycle(
            lines_used, excluded_quote_hashes=excluded_quote_hashes,
            allow_cycle_reset=allow_cycle_reset,
        ))

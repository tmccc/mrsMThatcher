"""Original-image selection for regular and experimental posts.

The coordinator supplies current helpers, settings, exception classes and logger.
Fixed topic weighting, calendar eligibility and diagnostics use the scoring owner.
Explicit calls discover and recheck metadata, preserve the shared random stream,
and mutate caller histories/counters at the existing cycle and receipt boundaries.
Legacy normalization saves through the supplied root callback before checking for
remaining integer entries. Durable persistence, receipt implementations, publishing
and configuration authority stay in the coordinator. Importing this module does
no runtime work. ImageSelection binds current external policy/helpers per root
call, then invokes owned eligibility, scored-pool choice and logging directly
without retaining caller state. Pair orchestration keeps the root matched-image
callback so each attempt binds current image policy after quotation selection.
"""

from __future__ import annotations

from dataclasses import dataclass

import random
from collections.abc import Callable
from logging import Logger
from pathlib import Path

from mrs_bot_image_scoring import (
    build_image_topic_idf,
    concise_components,
    image_is_out_of_season,
)


@dataclass(frozen=True)
class ImageSelection:
    """Own eligible-image cycles, checked selection and selection diagnostics."""

    NoEligibleImageForQuote: type[Exception]
    log: Logger
    current_image_paths: Callable
    load_image_analysis: Callable
    normalise_image_used_basenames: Callable
    save_image_used_basenames: Callable
    image_used_history_has_legacy_indices: Callable
    current_datetime: Callable
    image_metadata_for_basename: Callable
    score_image_for_quote: Callable
    original_editorial_enabled: bool
    original_editorial_shadow_result: Callable
    apply_original_editorial_selection: Callable
    log_original_editorial_shadow_result: Callable
    image_glob: str
    images_used_file: Path
    UnsafeImageHistoryMigration: type[Exception]
    GlobalImageUnavailable: type[Exception]
    StaleImageMetadata: type[Exception]
    QuoteSpecificImageMismatch: type[Exception]

    def available_basenames(
        self,
        eligible_basenames: set[str],
        images_used: set[str],
        state: dict | None = None,
    ) -> tuple[list[str], bool]:
        """Return whether available currently eligible image basenames."""
        if not eligible_basenames:
            raise self.NoEligibleImageForQuote("No currently eligible regular-post images are available")

        available = sorted(eligible_basenames.difference(images_used))
        cycle_reset = False

        if not available:
            self.log.info("All currently eligible regular-post images used; resetting eligible image cycle")
            for basename in eligible_basenames:
                images_used.discard(basename)
            available = sorted(eligible_basenames)
            cycle_reset = True

            last_name = str((state or {}).get("last_regular_image_filename") or "")
            if len(available) > 1 and last_name in available:
                available.remove(last_name)
                self.log.info("Temporarily excluded last regular image at eligible-cycle boundary: %s", last_name)

        return available, cycle_reset

    def log_choice(
        self,
        choice: dict,
    ) -> None:
        """Log regular image selection."""
        self.log.info(
            "REGULAR_IMAGE_SELECTED source=%s basename=%s score=%s origin_quote_hash=%s origin_quote_match=%s origin_quote_boost=%s",
            choice.get("image_source", "original"),
            choice.get("basename", ""),
            choice.get("score"),
            choice.get("origin_quote_hash") or "",
            str(bool(choice.get("origin_quote_match"))).lower(),
            choice.get("origin_quote_boost", 0),
        )

    def choose_matched(
        self,
        images_used: set,
        quote_choice: dict,
        state: dict,
        *,
        force_cycle_reset: bool = False,
        avoid_last_image_at_cycle_boundary: bool = True,
        cycle_boundary_exclusions: set[str] | None = None,
        selection_phase: str = 'normal',
    ) -> dict:
        """Select the highest-scoring eligible unused image for a quotation."""
        images = self.current_image_paths()
        self.log.debug("Found %d images matching %s", len(images), self.image_glob)
        if not images:
            raise RuntimeError(f"No images found matching {self.image_glob}")

        image_analysis = self.load_image_analysis()
        normalised, changed = self.normalise_image_used_basenames(images_used, images, image_analysis)
        if changed:
            images_used.clear()
            images_used.update(normalised)
            self.save_image_used_basenames(self.images_used_file, normalised)
        if self.image_used_history_has_legacy_indices(images_used):
            raise self.UnsafeImageHistoryMigration(
                "Image used-history still contains legacy integer entries; refusing regular image posting until full analysed corpus is visible"
            )

        image_by_name = {Path(path).name: path for path in images}
        image_number_by_path = {}
        for image_no, path in enumerate(images):
            image_number_by_path.setdefault(path, image_no)

        if image_analysis is None:
            raise self.GlobalImageUnavailable("Image analysis unavailable or invalid; refusing regular quote/image posting")

        today_mm_dd = self.current_datetime().strftime("%m-%d")
        idf = build_image_topic_idf(image_analysis)
        eligible_basenames: set[str] = set()
        seasonally_excluded = 0
        stale_excluded = 0
        for basename in image_by_name:
            try:
                _, analysis = self.image_metadata_for_basename(image_analysis, basename, image_by_name[basename])
            except self.StaleImageMetadata:
                stale_excluded += 1
                continue
            if analysis is not None and image_is_out_of_season(analysis, today_mm_dd):
                seasonally_excluded += 1
                self.log.info("Skipping image %s: seasonal image outside appropriate window", basename)
                continue
            eligible_basenames.add(basename)

        if not eligible_basenames:
            raise self.GlobalImageUnavailable("No analysed currently eligible regular-post images are available")

        if force_cycle_reset:
            self.log.info("Forcing eligible image cycle reset for regular quote/image pairing recovery")
            for basename in eligible_basenames:
                images_used.discard(basename)
            available = sorted(eligible_basenames)
            cycle_reset = True
        else:
            available, cycle_reset = self.available_basenames(eligible_basenames, images_used, state)
        last_name = str((state or {}).get("last_regular_image_filename") or "")
        should_exclude_last = (
            avoid_last_image_at_cycle_boundary
            and len(available) > 1
            and last_name in available
            and (force_cycle_reset or (cycle_boundary_exclusions is not None and last_name in cycle_boundary_exclusions))
        )
        if should_exclude_last:
            available.remove(last_name)
            if cycle_boundary_exclusions is not None:
                cycle_boundary_exclusions.add(last_name)
            self.log.info("Temporarily excluded last regular image at forced eligible-cycle boundary: %s", last_name)
        self.log.info(
            "Image cycle status: used_count=%d currently_eligible=%d remaining_count=%d seasonally_excluded=%d stale_excluded=%d cycle_reset=%s",
            len(images_used),
            len(eligible_basenames),
            len(available),
            seasonally_excluded,
            stale_excluded,
            cycle_reset,
        )

        if not available:
            raise self.GlobalImageUnavailable("No currently unused eligible regular-post images are available")

        scored: list[dict] = []
        for basename in available:
            try:
                image_hash, analysis = self.image_metadata_for_basename(image_analysis, basename, image_by_name[basename])
            except self.StaleImageMetadata:
                self.log.info("Skipping image %s: stale analysed content", basename)
                continue
            score, components, eligible = self.score_image_for_quote(quote_choice.get("analysis"), analysis, idf)
            if not eligible:
                self.log.info("Skipping image %s: strong visual mismatch with selected quote", basename)
                continue
            path = image_by_name[basename]
            scored.append(
                {
                    "image_no": image_number_by_path[path],
                    "path": path,
                    "basename": basename,
                    "image_hash": image_hash,
                    "score": score,
                    "components": components,
                    "cycle_reset": cycle_reset,
                    "image_source": "original",
                    "origin_quote_hash": None,
                    "origin_quote_match": False,
                    "origin_quote_boost": 0.0,
                }
            )

        if not scored:
            raise self.QuoteSpecificImageMismatch("No metadata-eligible regular-post images matched the selected quote")

        return self._select_scored_image(
            quote_choice, scored, selection_phase=selection_phase,
        )

    def _select_scored_image(
        self,
        quote_choice: dict,
        scored: list[dict],
        *,
        selection_phase: str,
    ) -> dict:
        """Choose once from the scored pool, then apply and report editorial policy."""
        best_score = max(float(item["score"]) for item in scored)
        tied = [item for item in scored if float(item["score"]) == best_score]
        baseline_choice = random.choice(tied)
        comparison = None
        if self.original_editorial_enabled:
            comparison = self.original_editorial_shadow_result(
                quote_choice,
                baseline_choice,
                scored,
                selection_phase=selection_phase,
            )
        chosen = self.apply_original_editorial_selection(
            quote_choice,
            baseline_choice,
            scored,
            selection_phase=selection_phase,
            comparison=comparison,
        )

        self.log.info(
            "Selected matched image basename=%s image_no=%d score=%.2f components=%s",
            chosen["basename"],
            chosen["image_no"],
            chosen["score"],
            concise_components(chosen["components"]),
        )
        self.log_choice(chosen)
        self.log_original_editorial_shadow_result(
            quote_choice,
            baseline_choice,
            scored,
            selection_phase=selection_phase,
            comparison=comparison,
        )
        for item in sorted(scored, key=lambda entry: float(entry["score"]), reverse=True)[:5]:
            self.log.debug(
                "Image match candidate basename=%s score=%.2f components=%s",
                item["basename"],
                item["score"],
                concise_components(item["components"]),
            )
        return chosen


def choose_regular_quote_image_pair(
    lines_used: set,
    images_used: set,
    state: dict,
    *,
    force_image_cycle_reset: bool = False,
    avoid_last_image_at_cycle_boundary: bool = True,
    excluded_quote_hashes: set[str] | None = None,
    choose_unused_line_candidate: Callable,
    choose_matched_unused_image: Callable,
    max_quote_image_pair_attempts: int,
    QuoteSpecificImageMismatch: type[Exception],
    NoViableQuoteImagePair: type[Exception],
    log: Logger,
) -> tuple[dict, dict, int]:
    """Select a production quotation-image pair under current cycle rules."""
    attempted_quote_hashes: set[str] = set(excluded_quote_hashes or set())
    initial_excluded_count = len(attempted_quote_hashes)
    attempts = 0
    reset_available_images_once = force_image_cycle_reset
    cycle_boundary_exclusions: set[str] = set()

    while attempts < max_quote_image_pair_attempts:
        attempts += 1
        try:
            # Only lasting exclusions can justify resetting the quote cycle.
            # Failed pairings must reach image recovery with history intact.
            quote_choice = choose_unused_line_candidate(
                lines_used,
                excluded_quote_hashes=attempted_quote_hashes,
                allow_cycle_reset=attempts == 1,
            )
        except RuntimeError:
            if len(attempted_quote_hashes) > initial_excluded_count:
                break
            raise
        attempted_quote_hashes.add(str(quote_choice["quote_hash"]))
        try:
            selection_phase = "normal"
            if force_image_cycle_reset:
                selection_phase = "forced_cycle_reset" if avoid_last_image_at_cycle_boundary else "last_image_fallback"
            image_choice = choose_matched_unused_image(
                images_used,
                quote_choice,
                state,
                force_cycle_reset=reset_available_images_once,
                avoid_last_image_at_cycle_boundary=avoid_last_image_at_cycle_boundary,
                cycle_boundary_exclusions=cycle_boundary_exclusions if force_image_cycle_reset else None,
                selection_phase=selection_phase,
            )
            if attempts > 1:
                log.info(
                    "Selected alternate quote/image pair after %d attempt(s). line_no=%s image=%s",
                    attempts,
                    quote_choice.get("line_no"),
                    image_choice.get("basename"),
                )
            return quote_choice, image_choice, attempts
        except QuoteSpecificImageMismatch as exc:
            log.warning(
                "Selected quote line_no=%s quote_hash=%s could not be paired with any currently eligible unused image: %s",
                quote_choice.get("line_no"),
                quote_choice.get("quote_hash"),
                exc,
            )
        finally:
            reset_available_images_once = False

    raise NoViableQuoteImagePair(
        f"No eligible regular quote/image pair found after {attempts} attempt(s); used histories unchanged",
        attempts,
        sorted(cycle_boundary_exclusions)[0] if cycle_boundary_exclusions else None,
    )

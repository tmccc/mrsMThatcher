"""Bind historical generated-image selection to the offline simulator.

Only this tool object owns the retired configuration, audit cache and selection
hooks. The supplied runtime retains ordinary quote scoring, image validation and
private-state persistence; no generated API is installed on its module.
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import mrs_bot_used_history as _used_history

from tools import _generated_image_simulation_identity as _identity
from tools import _generated_image_simulation_selection as _selection


class HistoricalImageSelection:
    """Keep the retired mixed-image policy available to saved experiments."""

    CONFIG_DEFAULTS = {
        "ENABLE_GENERATED_IMAGE_POOL": False,
        "GENERATED_IMAGE_DIR": "generated_review_approved_images",
        "GENERATED_IMAGE_GLOB": "*.png",
        "GENERATED_IMAGE_ANALYSIS_FILE": "generated_image_analysis.json",
        "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST": 4,
        "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": 2,
        "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING": False,
        "ENABLE_GENERATED_IDENTITY_POLICY_SCORING": False,
        "GENERATED_IDENTITY_AUDIT_FILE": "generated_image_identity_dependence_audit.json",
        "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY": 6.0,
        "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY": 15.0,
    }
    GENERATED_IDENTITY_AUDIT_KIND = "generated_image_identity_dependence_audit"
    GENERATED_IDENTITY_AUDIT_SCHEMA_VERSION = 1
    GENERATED_IDENTITY_POLICIES = {"unrestricted", "small_penalty", "strong_penalty", "origin_quote_only"}
    GENERATED_IDENTITY_DEPENDENCE_VALUES = {"none", "low", "medium", "high", "essential"}
    generated_identity_numeric = staticmethod(_identity.generated_identity_numeric)
    _choice_with_random_state = staticmethod(_identity._choice_with_random_state)

    def __init__(self, bot: Any):
        """Create isolated historical settings for one simulator runtime."""
        self.bot = bot
        for key, value in self.CONFIG_DEFAULTS.items():
            if key in {"GENERATED_IMAGE_DIR", "GENERATED_IMAGE_ANALYSIS_FILE", "GENERATED_IDENTITY_AUDIT_FILE"}:
                value = str(bot.BASE_DIR / value)
            setattr(self, key, value)
        self._GENERATED_IDENTITY_AUDIT_CACHE: dict[str, dict] = {}

    def snapshot_config(self, config: dict) -> dict:
        """Validate retired settings without extending live configuration."""
        candidate = {}
        for key, value in config.items():
            if key not in self.CONFIG_DEFAULTS:
                continue
            value = self.bot._coerce_local_config_value(key, value, getattr(self, key))
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f"{key} must be finite and non-negative")
            candidate[key] = value
        return candidate

    def merge_image_analysis(self, primary: dict, generated: dict | None) -> dict:
        """Merge image analysis."""
        return _selection.merge_image_analysis(
            primary,
            generated,
            log=self.bot.log,
        )

    def load_image_analysis(self) -> dict | None:
        """Load original and, when enabled, generated image metadata."""
        return _selection.load_image_analysis(
            image_analysis_file=self.bot.IMAGE_ANALYSIS_FILE,
            pool_enabled=self.ENABLE_GENERATED_IMAGE_POOL,
            generated_image_analysis_file=self.GENERATED_IMAGE_ANALYSIS_FILE,
            load_image_analysis_file=self.bot.load_image_analysis_file,
            merge_image_analysis=self.merge_image_analysis,
            log=self.bot.log,
        )

    def current_image_paths(self) -> list[str]:
        """Return the current image paths."""
        return _selection.current_image_paths(
            image_glob=self.bot.IMAGE_GLOB,
            pool_enabled=self.ENABLE_GENERATED_IMAGE_POOL,
            generated_image_dir=self.GENERATED_IMAGE_DIR,
            generated_image_glob=self.GENERATED_IMAGE_GLOB,
            glob=self.bot.glob,
            configured_generated_image_paths=self.configured_generated_image_paths,
            log=self.bot.log,
        )

    def configured_generated_image_paths(self) -> dict[str, Path]:
        """Return the configured generated image paths."""
        return _selection.configured_generated_image_paths(
            generated_image_dir=self.GENERATED_IMAGE_DIR,
            generated_image_glob=self.GENERATED_IMAGE_GLOB,
            glob=self.bot.glob,
            path_is_same_or_child=self.bot.path_is_same_or_child,
            generated_image_origin_quote_hash=self.bot.generated_image_origin_quote_hash,
        )

    def validate_generated_identity_audit_item(self, basename: str, item: object, image_by_name: dict[str, Path]) -> dict:
        """Validate generated identity audit item."""
        return _identity.validate_generated_identity_audit_item(
            basename, item, image_by_name,
            generated_image_origin_quote_hash=self.bot.generated_image_origin_quote_hash,
            file_sha256=self.bot.file_sha256,
            policies=self.GENERATED_IDENTITY_POLICIES,
            dependence_values=self.GENERATED_IDENTITY_DEPENDENCE_VALUES,
            generated_identity_numeric=self.generated_identity_numeric,
        )

    def load_generated_identity_audit(self) -> dict[str, dict]:
        """Load generated identity audit."""
        return _identity.load_generated_identity_audit(
            audit_file=self.GENERATED_IDENTITY_AUDIT_FILE,
            audit_cache=self._GENERATED_IDENTITY_AUDIT_CACHE,
            schema_version=self.GENERATED_IDENTITY_AUDIT_SCHEMA_VERSION,
            audit_kind=self.GENERATED_IDENTITY_AUDIT_KIND,
            configured_generated_image_paths=self.configured_generated_image_paths,
            validate_generated_identity_audit_item=self.validate_generated_identity_audit_item,
        )

    def generated_identity_policy_scoring_active(self) -> bool:
        """Return whether generated candidates can receive production policy scoring."""
        return bool(self.ENABLE_GENERATED_IMAGE_POOL and self.ENABLE_GENERATED_IDENTITY_POLICY_SCORING)

    def generated_identity_policy_shadow_active(self) -> bool:
        """Return whether generated candidates can receive observational policy scoring."""
        return bool(
            self.ENABLE_GENERATED_IMAGE_POOL
            and self.ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING
        )

    def generated_identity_candidate_shadow_row(self, candidate: dict, audit_by_basename: dict[str, dict]) -> dict:
        """Return the generated identity candidate shadow row."""
        return _identity.generated_identity_candidate_shadow_row(
            candidate, audit_by_basename,
            small_penalty=self.GENERATED_IDENTITY_SHADOW_SMALL_PENALTY,
            strong_penalty=self.GENERATED_IDENTITY_SHADOW_STRONG_PENALTY,
        )

    def generated_identity_policy_shadow_result(
        self,
        quote_choice: dict,
        production_choice: dict,
        scored_candidates: list[dict],
        *,
        selection_phase: str,
        audit_by_basename: dict[str, dict] | None = None,
        selection_rng_state: object | None = None,
    ) -> dict:
        """Evaluate generated-image identity policy without changing selection."""
        return _identity.generated_identity_policy_shadow_result(
            quote_choice, production_choice, scored_candidates,
            selection_phase=selection_phase,
            audit_by_basename=audit_by_basename,
            selection_rng_state=selection_rng_state,
            load_generated_identity_audit=self.load_generated_identity_audit,
            generated_identity_candidate_shadow_row=self.generated_identity_candidate_shadow_row,
            _choice_with_random_state=self._choice_with_random_state,
            small_penalty=self.GENERATED_IDENTITY_SHADOW_SMALL_PENALTY,
            strong_penalty=self.GENERATED_IDENTITY_SHADOW_STRONG_PENALTY,
        )

    def generated_identity_policy_selection(
        self,
        scored_candidates: list[dict],
        *,
        audit_by_basename: dict[str, dict] | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Return policy rows and eligible candidates without mutating input rows."""
        return _identity.generated_identity_policy_selection(
            scored_candidates,
            audit_by_basename=audit_by_basename,
            load_generated_identity_audit=self.load_generated_identity_audit,
            generated_identity_candidate_shadow_row=self.generated_identity_candidate_shadow_row,
        )

    def generated_identity_policy_applied_result(
        self,
        quote_choice: dict,
        production_winner: dict,
        baseline_candidates: list[dict],
        policy_rows: list[dict],
        policy_tie_count: int,
        *,
        selection_phase: str,
        selection_rng_state: object,
    ) -> dict:
        """Apply the enabled generated-image identity policy to scored candidates."""
        return _identity.generated_identity_policy_applied_result(
            quote_choice, production_winner, baseline_candidates, policy_rows, policy_tie_count,
            selection_phase=selection_phase,
            selection_rng_state=selection_rng_state,
            _choice_with_random_state=self._choice_with_random_state,
        )

    def log_generated_identity_policy_applied_result(self, payload: dict) -> None:
        """Log generated identity policy applied result."""
        return _identity.log_generated_identity_policy_applied_result(
            payload,
            log=self.bot.log,
        )

    def log_generated_identity_policy_shadow_result(
        self,
        quote_choice: dict,
        production_choice: dict,
        scored_candidates: list[dict],
        *,
        selection_phase: str,
        selection_rng_state: object | None = None,
    ) -> None:
        """Log generated identity policy shadow result."""
        return _identity.log_generated_identity_policy_shadow_result(
            quote_choice, production_choice, scored_candidates,
            selection_phase=selection_phase,
            selection_rng_state=selection_rng_state,
            generated_identity_policy_shadow_active=self.generated_identity_policy_shadow_active,
            generated_identity_policy_shadow_result=self.generated_identity_policy_shadow_result,
            log=self.bot.log,
        )

    def image_selection_observability(self, basename: str, quote_hash: object = None, origin_quote_boost: float = 0.0) -> dict:
        """Return the image selection observability."""
        return _selection.image_selection_observability(
            basename,
            quote_hash,
            origin_quote_boost,
            generated_image_origin_quote_hash=self.bot.generated_image_origin_quote_hash,
        )

    def generated_image_spacing_required(self) -> int:
        """Return the generated image spacing required."""
        return _selection.generated_image_spacing_required(
            min_original_posts_between=self.GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN,
        )

    def original_posts_since_generated_image(self, state: dict | None) -> int:
        """Return the original posts since generated image."""
        return _selection.original_posts_since_generated_image(
            state,
            generated_image_spacing_required=self.generated_image_spacing_required,
        )

    def generated_images_allowed_by_spacing(self, state: dict | None) -> bool:
        """Return whether generated images allowed by spacing."""
        return _selection.generated_images_allowed_by_spacing(
            state,
            generated_image_spacing_required=self.generated_image_spacing_required,
            original_posts_since_generated_image=self.original_posts_since_generated_image,
        )

    def log_generated_image_spacing_status(self, state: dict | None) -> bool:
        """Log generated image spacing status."""
        return _selection.log_generated_image_spacing_status(
            state,
            generated_image_spacing_required=self.generated_image_spacing_required,
            original_posts_since_generated_image=self.original_posts_since_generated_image,
            generated_images_allowed_by_spacing=self.generated_images_allowed_by_spacing,
            enable_generated_image_pool=self.ENABLE_GENERATED_IMAGE_POOL,
            log=self.bot.log,
        )

    def log_generated_image_spacing_state_updated(self, state: dict | None, image_basename: str) -> None:
        """Log generated image spacing state updated."""
        return _selection.log_generated_image_spacing_state_updated(
            state,
            image_basename,
            generated_image_spacing_required=self.generated_image_spacing_required,
            original_posts_since_generated_image=self.original_posts_since_generated_image,
            generated_images_allowed_by_spacing=self.generated_images_allowed_by_spacing,
            generated_image_origin_quote_hash=self.bot.generated_image_origin_quote_hash,
            enable_generated_image_pool=self.ENABLE_GENERATED_IMAGE_POOL,
            log=self.bot.log,
        )

    def filter_generated_images_by_spacing(self, eligible_basenames: set[str], state: dict | None) -> set[str]:
        """Filter generated images by spacing."""
        return _selection.filter_generated_images_by_spacing(
            eligible_basenames,
            state,
            generated_images_allowed_by_spacing=self.generated_images_allowed_by_spacing,
            generated_image_origin_quote_hash=self.bot.generated_image_origin_quote_hash,
            enable_generated_image_pool=self.ENABLE_GENERATED_IMAGE_POOL,
        )

    def update_regular_generated_image_spacing_state(self, state: dict, image_basename: str) -> None:
        """Update regular generated image spacing state."""
        return _selection.update_regular_generated_image_spacing_state(
            state,
            image_basename,
            generated_image_spacing_required=self.generated_image_spacing_required,
            generated_image_origin_quote_hash=self.bot.generated_image_origin_quote_hash,
            original_posts_since_generated_image=self.original_posts_since_generated_image,
            log_generated_image_spacing_state_updated=self.log_generated_image_spacing_state_updated,
        )

    def normalise_image_used_basenames(
        self, images_used: set, images: list[str], image_analysis: dict | None = None,
    ) -> tuple[set, bool]:
        """Keep legacy numeric history unresolved for a mixed-image corpus."""
        return _used_history.normalise_image_used_basenames(
            images_used, images, image_analysis,
            Path=Path,
            image_corpus_verified_for_legacy_migration=lambda paths, analysis: (
                not self.ENABLE_GENERATED_IMAGE_POOL
                and self.bot.image_corpus_verified_for_legacy_migration(paths, analysis)
            ),
            re=self.bot.re,
        )

    def choose_matched_unused_image(
        self,
        images_used: set,
        quote_choice: dict,
        state: dict,
        *,
        force_cycle_reset: bool = False,
        avoid_last_image_at_cycle_boundary: bool = True,
        cycle_boundary_exclusions: set[str] | None = None,
        generated_images_allowed: bool | None = None,
        selection_phase: str = "normal",
    ) -> dict:
        """Select the highest-scoring eligible unused image for a quotation."""
        return _selection.choose_matched_unused_image(
            images_used,
            quote_choice,
            state,
            force_cycle_reset=force_cycle_reset,
            avoid_last_image_at_cycle_boundary=avoid_last_image_at_cycle_boundary,
            cycle_boundary_exclusions=cycle_boundary_exclusions,
            generated_images_allowed=generated_images_allowed,
            selection_phase=selection_phase,
            current_image_paths=self.current_image_paths,
            load_image_analysis=self.load_image_analysis,
            normalise_image_used_basenames=self.normalise_image_used_basenames,
            save_image_used_basenames=self.bot.save_image_used_basenames,
            image_used_history_has_legacy_indices=self.bot.image_used_history_has_legacy_indices,
            current_datetime=self.bot.current_datetime,
            build_image_topic_idf=self.bot.build_image_topic_idf,
            image_metadata_for_basename=self.bot.image_metadata_for_basename,
            image_is_out_of_season=self.bot.image_is_out_of_season,
            generated_images_allowed_by_spacing=self.generated_images_allowed_by_spacing,
            filter_generated_images_by_spacing=self.filter_generated_images_by_spacing,
            available_currently_eligible_image_basenames=self.bot.available_currently_eligible_image_basenames,
            score_image_for_quote=self.bot.score_image_for_quote,
            generated_image_origin_quote_hash=self.bot.generated_image_origin_quote_hash,
            image_selection_observability=self.image_selection_observability,
            generated_identity_policy_scoring_active=self.generated_identity_policy_scoring_active,
            generated_identity_policy_selection=self.generated_identity_policy_selection,
            apply_original_editorial_selection=self.bot.apply_original_editorial_selection,
            concise_components=self.bot.concise_components,
            log_regular_image_selection=self.bot.log_regular_image_selection,
            log_original_editorial_shadow_result=self.bot.log_original_editorial_shadow_result,
            generated_identity_policy_applied_result=self.generated_identity_policy_applied_result,
            log_generated_identity_policy_applied_result=self.log_generated_identity_policy_applied_result,
            log_generated_identity_policy_shadow_result=self.log_generated_identity_policy_shadow_result,
            image_glob=self.bot.IMAGE_GLOB,
            images_used_file=self.bot.IMAGES_USED_FILE,
            generated_image_origin_quote_boost=self.GENERATED_IMAGE_ORIGIN_QUOTE_BOOST,
            UnsafeImageHistoryMigration=self.bot.UnsafeImageHistoryMigration,
            GlobalImageUnavailable=self.bot.GlobalImageUnavailable,
            StaleImageMetadata=self.bot.StaleImageMetadata,
            QuoteSpecificImageMismatch=self.bot.QuoteSpecificImageMismatch,
            log=self.bot.log,
        )

    def choose_regular_quote_image_pair(
        self,
        lines_used: set,
        images_used: set,
        state: dict,
        *,
        force_image_cycle_reset: bool = False,
        avoid_last_image_at_cycle_boundary: bool = True,
        excluded_quote_hashes: set[str] | None = None,
    ) -> tuple[dict, dict, int]:
        """Select a production quotation-image pair under current cycle rules."""
        return _selection.choose_regular_quote_image_pair(
            lines_used,
            images_used,
            state,
            force_image_cycle_reset=force_image_cycle_reset,
            avoid_last_image_at_cycle_boundary=avoid_last_image_at_cycle_boundary,
            excluded_quote_hashes=excluded_quote_hashes,
            log_generated_image_spacing_status=self.log_generated_image_spacing_status,
            choose_unused_line_candidate=self.bot.choose_unused_line_candidate,
            choose_matched_unused_image=self.choose_matched_unused_image,
            max_quote_image_pair_attempts=self.bot.MAX_QUOTE_IMAGE_PAIR_ATTEMPTS,
            QuoteSpecificImageMismatch=self.bot.QuoteSpecificImageMismatch,
            NoViableQuoteImagePair=self.bot.NoViableQuoteImagePair,
            log=self.bot.log,
        )


@lru_cache(maxsize=1)
def historical_image_selection(bot: Any) -> HistoricalImageSelection:
    """Return the simulator's image policy owner for its imported runtime."""
    return HistoricalImageSelection(bot)

"""Original-editorial metadata validation, scoring and winner application.

The bot supplies current configuration, vocabulary, cache and AssetMetadata.
Fixed tag/list normalization and legacy generated-name exclusion come from their owners.
Only explicit loader calls read metadata and discover/hash images; enabled
startup/selection calls emit the existing logs through the supplied logger.
OriginalEditorial binds these current boundaries without runtime work, then calls
its owned concepts, validation, loading, scores and comparisons and the supplied
metadata owner directly. Caller cache/vocabulary references and their existing
mutation rules are preserved.
"""

from __future__ import annotations

from dataclasses import dataclass

import json
import math
from logging import Logger
from pathlib import Path

from mrs_bot_asset_metadata import AssetMetadata, generated_image_origin_quote_hash
from mrs_bot_image_scoring import as_string_list, normalise_tag


def original_editorial_numeric(value: object, *, key: str) -> float:
    """Return the original editorial numeric."""
    if isinstance(value, bool):
        raise ValueError(f"{key} must be numeric in 0..10, got boolean")
    try:
        number = float(value)
    except Exception as exc:
        raise ValueError(f"{key} must be numeric in 0..10") from exc
    if not math.isfinite(number):
        raise ValueError(f"{key} must be finite")
    if not 0.0 <= number <= 10.0:
        raise ValueError(f"{key} outside 0..10: {number}")
    return number


@dataclass(frozen=True)
class OriginalEditorial:
    """Own editorial validation, scoring and comparison through AssetMetadata."""

    synonym_to_concept: dict[str, str]
    affinity_concepts: set[str]
    dimensions: list[str]
    metadata: AssetMetadata
    analysis_file: str | Path
    analysis_cache: dict[str, dict]
    analysis_kind: str
    schema_version: int
    enabled: bool
    default_weight: float
    default_max_abs_adjustment: float
    log: Logger

    def concepts(
        self,
        value: object,
    ) -> set[str]:
        """Return the original editorial concepts."""
        tag = normalise_tag(value)
        if not tag:
            return set()
        if "_and_" in tag:
            concepts: set[str] = set()
            for part in tag.split("_and_"):
                concepts.update(self.concepts(part))
            return concepts
        concept = self.synonym_to_concept.get(tag)
        if concept in self.affinity_concepts:
            return {concept}
        return set()

    def quote_concepts(
        self,
        quote_analysis: dict | None,
    ) -> set[str]:
        """Return the original editorial quote concepts."""
        if not isinstance(quote_analysis, dict):
            return set()
        concepts: set[str] = set()
        fields: list[object] = []
        fields.extend(as_string_list(quote_analysis.get("primary_topics")))
        fields.extend(as_string_list(quote_analysis.get("secondary_topics")))
        fields.extend(as_string_list(quote_analysis.get("tone")))
        prefs = quote_analysis.get("archive_image_preferences", {}) if isinstance(quote_analysis.get("archive_image_preferences"), dict) else {}
        for key in ("preferred_subject_moods", "preferred_scenes", "preferred_activities", "preferred_visible_symbols", "visual_affinities"):
            fields.extend(as_string_list(prefs.get(key)))
        hist = quote_analysis.get("historical_context", {}) if isinstance(quote_analysis.get("historical_context"), dict) else {}
        for key in ("referenced_events", "referenced_people", "referenced_places", "specificity"):
            fields.extend(as_string_list(hist.get(key)))
        for value in fields:
            concepts.update(self.concepts(value))
        return concepts

    def image_concepts(
        self,
        editorial: dict | None,
    ) -> set[str]:
        """Return the original editorial image concepts."""
        if not isinstance(editorial, dict):
            return set()
        concepts: set[str] = set()
        for key in ("abstract_quote_affinities", "editorial_functions", "best_quote_types"):
            for value in as_string_list(editorial.get(key)):
                concepts.update(self.concepts(value))
        return concepts

    def avoid_concepts(
        self,
        editorial: dict | None,
    ) -> set[str]:
        """Return the original editorial avoid concepts."""
        if not isinstance(editorial, dict):
            return set()
        concepts: set[str] = set()
        for value in as_string_list(editorial.get("avoid_quote_types")):
            concepts.update(self.concepts(value))
        return concepts

    def quote_profile(
        self,
        quote_analysis: dict | None,
    ) -> dict[str, float]:
        """Return the original editorial quote dimension profile."""
        if not isinstance(quote_analysis, dict):
            return {dim: 0.0 for dim in self.dimensions}
        concepts = {normalise_tag(value) for value in as_string_list(quote_analysis.get("primary_topics")) + as_string_list(quote_analysis.get("secondary_topics"))}
        controlled = self.quote_concepts(quote_analysis)
        tone = {normalise_tag(value) for value in as_string_list(quote_analysis.get("tone"))}
        prefs = quote_analysis.get("archive_image_preferences", {}) if isinstance(quote_analysis.get("archive_image_preferences"), dict) else {}
        visual_energy = str(quote_analysis.get("visual_energy") or "").lower()
        hist = quote_analysis.get("historical_context", {}) if isinstance(quote_analysis.get("historical_context"), dict) else {}
        profile = {dim: 0.0 for dim in self.dimensions}

        def add(dim: str, value: float) -> None:
            profile[dim] = min(1.0, max(profile[dim], value))

        if controlled & {"freedom", "duty", "responsibility"}:
            add("conviction", 0.65)
        if controlled & {"warning"} or tone & {"grave", "urgent", "warning"}:
            add("warning", 0.7)
        if controlled & {"defiance"} or tone & {"defiant", "confrontational"}:
            add("defiance", 0.65)
        if controlled & {"patriotism", "national_identity"}:
            add("patriotism", 0.65)
        if controlled & {"economic", "enterprise"}:
            add("economic_seriousness", 0.75)
        if controlled & {"family"} or tone & {"warm", "personal"}:
            add("human_warmth", 0.7)
        if controlled & {"ceremony"}:
            add("ceremony_formality", 0.55)
        if "socialism" in controlled:
            add("conviction", 0.45)
            add("economic_seriousness", 0.45)
        if concepts & {"government", "law_and_order"} or tone & {"authoritative"}:
            add("authority", 0.45)
        if concepts & {"diplomacy", "parliament"}:
            add("statesmanship", 0.5)
        if hist.get("needs_historical_image_match") or str(hist.get("specificity")) in {"specific", "high"}:
            add("historical_iconicity", 0.7)
        if visual_energy == "high":
            add("defiance", 0.35)
        if visual_energy == "low":
            add("statesmanship", 0.25)
        for scene in as_string_list(prefs.get("preferred_scenes")):
            scene_tag = normalise_tag(scene)
            if scene_tag in {"parliament", "office_or_working"}:
                add("statesmanship", 0.45)
            if scene_tag == "formal_portrait":
                add("authority", 0.3)
        return profile

    def validate_item(
        self,
        basename: str,
        entry: dict,
        image_by_name: dict[str, str],
    ) -> dict:
        """Validate original editorial item."""
        if generated_image_origin_quote_hash(basename):
            raise ValueError(f"generated-style basename is not allowed in original editorial analysis: {basename}")
        if Path(basename).suffix.lower() == ".png":
            raise ValueError(f"PNG basename is not allowed in original editorial analysis: {basename}")
        if basename not in image_by_name:
            raise ValueError(f"original editorial image is not present in current image corpus: {basename}")
        expected_sha = str(entry.get("sha256") or "")
        if not expected_sha:
            raise ValueError(f"missing sha256 for original editorial image {basename}")
        current_sha = self.metadata.image_sha256(image_by_name[basename])
        if current_sha != expected_sha:
            raise ValueError(f"stale SHA-256 for original editorial image {basename}")
        analysis = entry.get("analysis")
        if not isinstance(analysis, dict):
            raise ValueError(f"missing analysis for original editorial image {basename}")
        dims = analysis.get("dimension_scores")
        if not isinstance(dims, dict):
            raise ValueError(f"dimension_scores for {basename} is not an object")
        required_dims = set(self.dimensions)
        actual_dims = set(dims)
        missing_dims = sorted(required_dims - actual_dims)
        unexpected_dims = sorted(actual_dims - required_dims)
        if missing_dims or unexpected_dims:
            details = []
            if missing_dims:
                details.append("missing dimensions: " + ", ".join(missing_dims))
            if unexpected_dims:
                details.append("unexpected dimensions: " + ", ".join(unexpected_dims))
            raise ValueError(f"invalid dimension_scores schema for {basename}: {'; '.join(details)}")
        for dim, value in dims.items():
            original_editorial_numeric(value, key=f"{basename}.dimension_scores.{dim}")
        original_editorial_numeric(analysis.get("overall_editorial_utility", 5.5), key=f"{basename}.overall_editorial_utility")
        return analysis

    def load(
        self,
    ) -> dict[str, dict]:
        """Load original editorial analysis."""
        path = Path(str(self.analysis_file)).expanduser()
        cache_key = str(path)
        if cache_key in self.analysis_cache:
            return self.analysis_cache[cache_key]
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise RuntimeError(f"Original editorial analysis file is not a JSON object: {path}")
        if data.get("analysis_kind") != self.analysis_kind:
            raise RuntimeError(f"Original editorial analysis has unexpected analysis_kind={data.get('analysis_kind')!r}")
        if (type(data.get("schema_version")) is not int
                or data.get("schema_version") != self.schema_version):
            raise RuntimeError(f"Original editorial analysis has unsupported schema_version={data.get('schema_version')!r}")
        items = data.get("items")
        if not isinstance(items, dict):
            raise RuntimeError("Original editorial analysis items must be an object")
        image_by_name = {Path(path_text).name: path_text for path_text in self.metadata.image_paths()}
        result: dict[str, dict] = {}
        try:
            for key, entry in items.items():
                if not isinstance(entry, dict):
                    raise ValueError(f"invalid item for {key}")
                basename = str(entry.get("basename") or key)
                result[basename] = self.validate_item(basename, entry, image_by_name)
            missing_originals = sorted(
                basename
                for basename in image_by_name
                if not generated_image_origin_quote_hash(basename)
                and Path(basename).suffix.lower() != ".png"
                and basename not in result
            )
            if missing_originals:
                raise ValueError(f"missing original editorial analysis for current image(s): {', '.join(missing_originals[:5])}")
        except ValueError as exc:
            raise RuntimeError(f"Invalid original editorial analysis {path}: {exc}") from exc
        self.analysis_cache[cache_key] = result
        return result

    def validate_startup(
        self,
    ) -> None:
        """Validate original editorial shadow startup."""
        if not self.enabled:
            return
        count = len(self.load())
        self.log.info(
            "Original editorial selection enabled. analysis_file=%s original_items=%d weight=%s max_abs_adjustment=%s",
            self.analysis_file,
            count,
            self.default_weight,
            self.default_max_abs_adjustment,
        )

    def score(
        self,
        quote_analysis: dict | None,
        editorial: dict | None,
        *,
        weight: float | None = None,
        max_abs_adjustment: float | None = None,
    ) -> tuple[float, dict]:
        """Calculate the observational editorial adjustment for one image."""
        if not isinstance(editorial, dict):
            return 0.0, {"dimension_score": 0.0, "affinity_score": 0.0, "utility_adjustment": 0.0, "penalty": 0.0, "cap_hit": False}
        weight = self.default_weight if weight is None else float(weight)
        max_abs_adjustment = self.default_max_abs_adjustment if max_abs_adjustment is None else float(max_abs_adjustment)
        q_profile = self.quote_profile(quote_analysis)
        raw_dims = editorial.get("dimension_scores") if isinstance(editorial.get("dimension_scores"), dict) else {}
        dimension_terms: list[dict] = []
        dimension_score = 0.0
        for dim, q_value in q_profile.items():
            if q_value <= 0:
                continue
            image_value = original_editorial_numeric(raw_dims.get(dim, 0), key=f"dimension_scores.{dim}") / 10.0
            contribution = q_value * (image_value - 0.45) * 5.0
            dimension_score += contribution
            dimension_terms.append(
                {
                    "dimension": dim,
                    "quote": round(q_value, 3),
                    "image": round(image_value, 3),
                    "contribution": round(contribution, 3),
                }
            )
        if q_profile.get("warning", 0) > 0.55 and original_editorial_numeric(raw_dims.get("optimism", 0), key="dimension_scores.optimism") / 10.0 > 0.75:
            dimension_score -= 1.5
            dimension_terms.append({"dimension": "optimism_warning_tension", "contribution": -1.5})
        if q_profile.get("defiance", 0) > 0.45 and original_editorial_numeric(raw_dims.get("ceremony_formality", 0), key="dimension_scores.ceremony_formality") / 10.0 > 0.85:
            dimension_score -= 0.8
            dimension_terms.append({"dimension": "ceremony_action_tension", "contribution": -0.8})

        q_concepts = self.quote_concepts(quote_analysis)
        image_concepts = self.image_concepts(editorial)
        avoid = self.avoid_concepts(editorial)
        affinity_matches = sorted(q_concepts & image_concepts)
        avoid_matches = sorted(q_concepts & avoid)
        affinity_score = min(4.0, 1.0 * len(affinity_matches))
        penalty = 1.25 * len(avoid_matches)
        utility = original_editorial_numeric(editorial.get("overall_editorial_utility", 5.5), key="overall_editorial_utility")
        utility_adjustment = max(-0.5, min(1.0, (utility - 5.5) / 4.5))
        raw_layer = dimension_score + affinity_score + utility_adjustment - penalty
        weighted = raw_layer * weight
        capped = max(-max_abs_adjustment, min(max_abs_adjustment, weighted))
        return capped, {
            "dimension_score": round(dimension_score, 4),
            "affinity_score": round(affinity_score, 4),
            "utility_adjustment": round(utility_adjustment, 4),
            "penalty": round(penalty, 4),
            "raw_layer": round(raw_layer, 4),
            "weighted_adjustment": round(weighted, 4),
            "capped_editorial_adjustment": round(capped, 4),
            "cap_hit": abs(capped - weighted) > 1e-9,
            "dimension_matches": [term["dimension"] for term in dimension_terms if term.get("contribution", 0) > 0],
            "dimension_terms": dimension_terms,
            "affinity_matches": affinity_matches,
            "penalties": avoid_matches,
        }

    def compare(
        self,
        quote_choice: dict,
        production_choice: dict,
        scored_candidates: list[dict],
        *,
        selection_phase: str,
    ) -> tuple[dict | None, dict | None]:
        """Return the existing editorial comparison and its preferred original."""
        editorial_by_basename = self.load()
        original_rows: list[dict] = []
        for candidate in scored_candidates:
            if candidate.get("image_source") != "original":
                continue
            basename = str(candidate.get("basename") or "")
            editorial = editorial_by_basename.get(basename)
            if not editorial:
                continue
            adjustment, detail = self.score(quote_choice.get("analysis"), editorial)
            baseline = float(candidate.get("score") or 0.0)
            original_rows.append(
                {
                    "basename": basename,
                    "baseline_score": baseline,
                    "editorial_adjustment": adjustment,
                    "shadow_score": baseline + adjustment,
                    "detail": detail,
                }
            )
        if not original_rows:
            return None, None
        original_rows.sort(key=lambda row: (-float(row["shadow_score"]), row["basename"]))
        for idx, row in enumerate(original_rows, 1):
            row["shadow_rank"] = idx
        shadow_winner = original_rows[0]
        production_basename = str(production_choice.get("basename") or "")
        production_shadow = next((row for row in original_rows if row["basename"] == production_basename), None)
        winner_changed = production_shadow is not None and shadow_winner["basename"] != production_basename
        payload = {
            "quote_hash": str(quote_choice.get("quote_hash") or ""),
            "line_no": int(quote_choice.get("line_no", -1)),
            "selection_phase": selection_phase,
            "production_source": str(production_choice.get("image_source") or "original"),
            "production_winner": production_basename,
            "production_baseline_score": round(float(production_choice.get("score") or 0.0), 4),
            "production_editorial_adjustment": round(float(production_shadow["editorial_adjustment"]), 4) if production_shadow else None,
            "production_shadow_score": round(float(production_shadow["shadow_score"]), 4) if production_shadow else None,
            "production_shadow_rank": int(production_shadow["shadow_rank"]) if production_shadow else None,
            "shadow_original_winner": shadow_winner["basename"],
            "shadow_winner_baseline_score": round(float(shadow_winner["baseline_score"]), 4),
            "shadow_winner_editorial_adjustment": round(float(shadow_winner["editorial_adjustment"]), 4),
            "shadow_winner_score": round(float(shadow_winner["shadow_score"]), 4),
            "winner_changed": bool(winner_changed),
            "eligible_original_count": len(original_rows),
            "weight": float(self.default_weight),
            "max_abs_adjustment": float(self.default_max_abs_adjustment),
            "cap_hit": bool(shadow_winner["detail"].get("cap_hit") or (production_shadow or {}).get("detail", {}).get("cap_hit")),
            "dimension_matches": shadow_winner["detail"].get("dimension_matches", [])[:8],
            "affinity_matches": shadow_winner["detail"].get("affinity_matches", [])[:8],
            "penalties": shadow_winner["detail"].get("penalties", [])[:8],
        }
        return payload, shadow_winner

    def log_comparison(
        self,
        quote_choice: dict,
        production_choice: dict,
        scored_candidates: list[dict],
        *,
        selection_phase: str,
        comparison: tuple[dict | None, dict | None] | None = None,
    ) -> None:
        """Log a prepared comparison, computing it for standalone callers if absent."""
        if not self.enabled:
            return
        if comparison is None:
            comparison = self.compare(
                quote_choice,
                production_choice,
                scored_candidates,
                selection_phase=selection_phase,
            )
        payload, _ = comparison
        if payload is not None:
            self.log.info("ORIGINAL_EDITORIAL_SHADOW_RESULT %s", json.dumps(payload, sort_keys=True, separators=(",", ":")))

    def apply_selection(
        self,
        quote_choice: dict,
        baseline_choice: dict,
        scored_candidates: list[dict],
        *,
        selection_phase: str,
        comparison: tuple[dict | None, dict | None] | None = None,
    ) -> dict:
        """Apply a prepared comparison, computing it for standalone callers if absent."""
        if not self.enabled:
            return baseline_choice
        if comparison is None:
            comparison = self.compare(
                quote_choice,
                baseline_choice,
                scored_candidates,
                selection_phase=selection_phase,
            )
        payload, editorial_winner = comparison
        if payload is None or editorial_winner is None:
            return baseline_choice

        # Selection-only fields must not leak into the shared shadow diagnostic.
        payload = dict(payload)

        # The process cache may predate a newly catalogued baseline. An absent
        # editorial row is not evidence that an older original is a better choice.
        selection_applied = (
            payload["production_source"] == "original"
            and payload.get("production_shadow_rank") is not None
        )
        payload["selection_applied"] = selection_applied
        payload["selected_winner"] = (
            editorial_winner["basename"]
            if selection_applied
            else baseline_choice.get("basename")
        )
        self.log.info(
            "ORIGINAL_EDITORIAL_SELECTION_RESULT %s",
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
        )
        if not selection_applied:
            return baseline_choice

        replacement = next(
            candidate
            for candidate in scored_candidates
            if candidate.get("basename") == editorial_winner["basename"]
        )
        selected = dict(replacement)
        selected["baseline_score"] = float(editorial_winner["baseline_score"])
        selected["original_editorial_adjustment"] = float(
            editorial_winner["editorial_adjustment"]
        )
        selected["score"] = float(editorial_winner["shadow_score"])
        selected["components"] = dict(selected.get("components") or {})
        selected["components"]["original_editorial"] = float(
            editorial_winner["editorial_adjustment"]
        )
        return selected

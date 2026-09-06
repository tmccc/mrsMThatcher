"""Regular and experimental image selection and generated-image spacing.

The coordinator supplies current helpers, settings, exception classes and logger.
Explicit calls discover and recheck metadata, preserve the shared random stream,
and mutate caller histories/counters at the existing cycle and receipt boundaries.
Legacy normalization saves through the supplied root callback before checking for
remaining integer entries. Durable persistence, receipt implementations, publishing
and configuration authority stay in the coordinator. Importing this module does
no runtime work and retains no callbacks or state.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from logging import Logger
from pathlib import Path


def available_currently_eligible_image_basenames(
    eligible_basenames: set[str],
    images_used: set[str],
    state: dict | None = None,
    *,
    NoEligibleImageForQuote: type[Exception],
    log: Logger,
) -> tuple[list[str], bool]:
    """Return whether available currently eligible image basenames."""
    if not eligible_basenames:
        raise NoEligibleImageForQuote("No currently eligible regular-post images are available")

    available = sorted(eligible_basenames.difference(images_used))
    cycle_reset = False

    if not available:
        log.info("All currently eligible regular-post images used; resetting eligible image cycle")
        for basename in eligible_basenames:
            images_used.discard(basename)
        available = sorted(eligible_basenames)
        cycle_reset = True

        last_name = str((state or {}).get("last_regular_image_filename") or "")
        if len(available) > 1 and last_name in available:
            available.remove(last_name)
            log.info("Temporarily excluded last regular image at eligible-cycle boundary: %s", last_name)

    return available, cycle_reset


def image_selection_observability(
    basename: str,
    quote_hash: object = None,
    origin_quote_boost: float = 0.0,
    *,
    generated_image_origin_quote_hash: Callable,
) -> dict:
    """Return the image selection observability."""
    origin_quote_hash = generated_image_origin_quote_hash(basename)
    origin_quote_match = bool(origin_quote_hash and origin_quote_hash == str(quote_hash or "").lower())
    return {
        "image_source": "generated" if origin_quote_hash else "original",
        "origin_quote_hash": origin_quote_hash,
        "origin_quote_match": origin_quote_match,
        "origin_quote_boost": float(origin_quote_boost if origin_quote_match else 0.0),
    }


def generated_image_spacing_required(
    *,
    min_original_posts_between: object,
) -> int:
    """Return the generated image spacing required."""
    if type(min_original_posts_between) is not int:
        raise ValueError("GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN must be an integer")
    if min_original_posts_between < 0:
        raise ValueError("GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN must be non-negative")
    return min_original_posts_between


def original_posts_since_generated_image(
    state: dict | None,
    *,
    generated_image_spacing_required: Callable,
) -> int:
    """Return the original posts since generated image."""
    if not state:
        return generated_image_spacing_required()
    try:
        value = int(state.get("original_regular_posts_since_generated_image", generated_image_spacing_required()) or 0)
    except Exception:
        value = 0
    return max(0, value)


def generated_images_allowed_by_spacing(
    state: dict | None,
    *,
    generated_image_spacing_required: Callable,
    original_posts_since_generated_image: Callable,
) -> bool:
    """Return whether generated images allowed by spacing."""
    required = generated_image_spacing_required()
    if required <= 0:
        return True
    return original_posts_since_generated_image(state) >= required


def log_generated_image_spacing_status(
    state: dict | None,
    *,
    generated_image_spacing_required: Callable,
    original_posts_since_generated_image: Callable,
    generated_images_allowed_by_spacing: Callable,
    enable_generated_image_pool: bool,
    log: Logger,
) -> bool:
    """Log generated image spacing status."""
    required = generated_image_spacing_required()
    count = original_posts_since_generated_image(state)
    allowed = generated_images_allowed_by_spacing(state)
    log.info(
        "GENERATED_IMAGE_SPACING_STATUS pool_enabled=%s allowed=%s original_posts_since_generated=%d required=%d",
        str(bool(enable_generated_image_pool)).lower(),
        str(bool(allowed)).lower(),
        count,
        required,
    )
    if enable_generated_image_pool and required > 0 and not allowed:
        log.info(
            "GENERATED_IMAGE_POOL_BLOCKED_BY_SPACING original_posts_since_generated=%d required=%d",
            count,
            required,
        )
    return allowed


def log_generated_image_spacing_state_updated(
    state: dict | None,
    image_basename: str,
    *,
    generated_image_spacing_required: Callable,
    original_posts_since_generated_image: Callable,
    generated_images_allowed_by_spacing: Callable,
    generated_image_origin_quote_hash: Callable,
    enable_generated_image_pool: bool,
    log: Logger,
) -> None:
    """Log generated image spacing state updated."""
    required = generated_image_spacing_required()
    count = original_posts_since_generated_image(state)
    allowed = generated_images_allowed_by_spacing(state)
    source = "generated" if generated_image_origin_quote_hash(image_basename) else "original"
    log.info(
        "GENERATED_IMAGE_SPACING_STATE_UPDATED pool_enabled=%s allowed=%s original_posts_since_generated=%d required=%d image_source=%s image=%s",
        str(bool(enable_generated_image_pool)).lower(),
        str(bool(allowed)).lower(),
        count,
        required,
        source,
        image_basename,
    )


def filter_generated_images_by_spacing(
    eligible_basenames: set[str],
    state: dict | None,
    *,
    generated_images_allowed_by_spacing: Callable,
    generated_image_origin_quote_hash: Callable,
    enable_generated_image_pool: bool,
) -> set[str]:
    """Filter generated images by spacing."""
    if not enable_generated_image_pool or generated_images_allowed_by_spacing(state):
        return eligible_basenames
    return {basename for basename in eligible_basenames if not generated_image_origin_quote_hash(basename)}


def update_regular_generated_image_spacing_state(
    state: dict,
    image_basename: str,
    *,
    generated_image_spacing_required: Callable,
    generated_image_origin_quote_hash: Callable,
    original_posts_since_generated_image: Callable,
    log_generated_image_spacing_state_updated: Callable,
) -> None:
    """Update regular generated image spacing state."""
    required = generated_image_spacing_required()
    if generated_image_origin_quote_hash(image_basename):
        state["original_regular_posts_since_generated_image"] = 0
        log_generated_image_spacing_state_updated(state, image_basename)
        return
    current = original_posts_since_generated_image(state)
    state["original_regular_posts_since_generated_image"] = min(required, current + 1) if required > 0 else 0
    log_generated_image_spacing_state_updated(state, image_basename)


def regular_generated_image_spacing_already_reflected(
    state: dict,
    image_basename: str,
    *,
    generated_image_origin_quote_hash: Callable,
    original_posts_since_generated_image: Callable,
) -> bool:
    """Return the regular generated image spacing already reflected."""
    if "original_regular_posts_since_generated_image" not in state:
        return False
    if generated_image_origin_quote_hash(image_basename):
        return original_posts_since_generated_image(state) == 0
    return True


def log_regular_image_selection(
    choice: dict,
    *,
    log: Logger,
) -> None:
    """Log regular image selection."""
    log.info(
        "REGULAR_IMAGE_SELECTED source=%s basename=%s score=%s origin_quote_hash=%s origin_quote_match=%s origin_quote_boost=%s",
        choice.get("image_source", "original"),
        choice.get("basename", ""),
        choice.get("score"),
        choice.get("origin_quote_hash") or "",
        str(bool(choice.get("origin_quote_match"))).lower(),
        choice.get("origin_quote_boost", 0),
    )


def choose_matched_unused_image(
    images_used: set,
    quote_choice: dict,
    state: dict,
    *,
    force_cycle_reset: bool = False,
    avoid_last_image_at_cycle_boundary: bool = True,
    cycle_boundary_exclusions: set[str] | None = None,
    generated_images_allowed: bool | None = None,
    selection_phase: str = 'normal',
    current_image_paths: Callable,
    load_image_analysis: Callable,
    normalise_image_used_basenames: Callable,
    save_image_used_basenames: Callable,
    image_used_history_has_legacy_indices: Callable,
    current_datetime: Callable,
    build_image_topic_idf: Callable,
    image_metadata_for_basename: Callable,
    image_is_out_of_season: Callable,
    generated_images_allowed_by_spacing: Callable,
    filter_generated_images_by_spacing: Callable,
    available_currently_eligible_image_basenames: Callable,
    score_image_for_quote: Callable,
    generated_image_origin_quote_hash: Callable,
    image_selection_observability: Callable,
    generated_identity_policy_scoring_active: Callable,
    generated_identity_policy_selection: Callable,
    apply_original_editorial_selection: Callable,
    concise_components: Callable,
    log_regular_image_selection: Callable,
    log_original_editorial_shadow_result: Callable,
    generated_identity_policy_applied_result: Callable,
    log_generated_identity_policy_applied_result: Callable,
    log_generated_identity_policy_shadow_result: Callable,
    image_glob: str,
    images_used_file: Path,
    generated_image_origin_quote_boost: float,
    UnsafeImageHistoryMigration: type[Exception],
    GlobalImageUnavailable: type[Exception],
    StaleImageMetadata: type[Exception],
    QuoteSpecificImageMismatch: type[Exception],
    log: Logger,
) -> dict:
    """Select the highest-scoring eligible unused image for a quotation."""
    images = current_image_paths()
    log.debug("Found %d images matching %s", len(images), image_glob)
    if not images:
        raise RuntimeError(f"No images found matching {image_glob}")

    image_analysis = load_image_analysis()
    normalised, changed = normalise_image_used_basenames(images_used, images, image_analysis)
    if changed:
        images_used.clear()
        images_used.update(normalised)
        save_image_used_basenames(images_used_file, normalised)
    if image_used_history_has_legacy_indices(images_used):
        raise UnsafeImageHistoryMigration(
            "Image used-history still contains legacy integer entries; refusing regular image posting until full analysed corpus is visible"
        )

    image_by_name = {Path(path).name: path for path in images}

    if image_analysis is None:
        raise GlobalImageUnavailable("Image analysis unavailable or invalid; refusing regular quote/image posting")

    today_mm_dd = current_datetime().strftime("%m-%d")
    idf = build_image_topic_idf(image_analysis)
    eligible_basenames: set[str] = set()
    seasonally_excluded = 0
    stale_excluded = 0
    for basename in image_by_name:
        try:
            _, analysis = image_metadata_for_basename(image_analysis, basename, image_by_name[basename])
        except StaleImageMetadata:
            stale_excluded += 1
            continue
        if analysis is not None and image_is_out_of_season(analysis, today_mm_dd):
            seasonally_excluded += 1
            log.info("Skipping image %s: seasonal image outside appropriate window", basename)
            continue
        eligible_basenames.add(basename)

    if generated_images_allowed is None:
        generated_images_allowed = generated_images_allowed_by_spacing(state)
    spacing_blocked_generated = 0
    if not generated_images_allowed:
        before_spacing = set(eligible_basenames)
        eligible_basenames = filter_generated_images_by_spacing(eligible_basenames, state)
        spacing_blocked_generated = len(before_spacing) - len(eligible_basenames)

    if not eligible_basenames:
        raise GlobalImageUnavailable("No analysed currently eligible regular-post images are available")

    if force_cycle_reset:
        log.info("Forcing eligible image cycle reset for regular quote/image pairing recovery")
        for basename in eligible_basenames:
            images_used.discard(basename)
        available = sorted(eligible_basenames)
        cycle_reset = True
    else:
        available, cycle_reset = available_currently_eligible_image_basenames(eligible_basenames, images_used, state)
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
        log.info("Temporarily excluded last regular image at forced eligible-cycle boundary: %s", last_name)
    log.info(
        "Image cycle status: used_count=%d currently_eligible=%d remaining_count=%d seasonally_excluded=%d stale_excluded=%d spacing_blocked_generated=%d cycle_reset=%s",
        len(images_used),
        len(eligible_basenames),
        len(available),
        seasonally_excluded,
        stale_excluded,
        spacing_blocked_generated,
        cycle_reset,
    )

    if not available:
        raise GlobalImageUnavailable("No currently unused eligible regular-post images are available")

    scored: list[dict] = []
    for basename in available:
        try:
            image_hash, analysis = image_metadata_for_basename(image_analysis, basename, image_by_name[basename])
        except StaleImageMetadata:
            log.info("Skipping image %s: stale analysed content", basename)
            continue
        score, components, eligible = score_image_for_quote(quote_choice.get("analysis"), analysis, idf)
        if not eligible:
            log.info("Skipping image %s: strong visual mismatch with selected quote", basename)
            continue
        origin_quote_hash = generated_image_origin_quote_hash(basename)
        origin_quote_boost = 0.0
        if origin_quote_hash and origin_quote_hash == str(quote_choice.get("quote_hash", "")).lower():
            boost = float(generated_image_origin_quote_boost)
            score += boost
            components = dict(components)
            components["generated_origin_quote"] = boost
            origin_quote_boost = boost
        path = image_by_name[basename]
        observability = image_selection_observability(
            basename,
            quote_choice.get("quote_hash"),
            origin_quote_boost,
        )
        scored.append(
            {
                "image_no": images.index(path),
                "path": path,
                "basename": basename,
                "image_hash": image_hash,
                "score": score,
                "components": components,
                "cycle_reset": cycle_reset,
                **observability,
            }
        )

    if not scored:
        raise QuoteSpecificImageMismatch("No metadata-eligible regular-post images matched the selected quote")

    baseline_best_score = max(float(item["score"]) for item in scored)
    baseline_tied = [item for item in scored if float(item["score"]) == baseline_best_score]
    policy_rows: list[dict] | None = None
    production_candidates = scored
    if generated_identity_policy_scoring_active():
        policy_rows, policy_candidates = generated_identity_policy_selection(scored)
        if not policy_candidates:
            log.warning(
                "Generated identity policy exhausted phase-specific candidates. line_no=%s quote_hash=%s phase=%s",
                quote_choice.get("line_no"),
                quote_choice.get("quote_hash"),
                selection_phase,
            )
            raise QuoteSpecificImageMismatch("Generated identity policy excluded all phase-specific candidates")
        best_score = max(float(item["score"]) for item in policy_candidates)
        tied = [item for item in policy_candidates if float(item["score"]) == best_score]
        production_candidates = policy_candidates
    else:
        best_score = baseline_best_score
        tied = baseline_tied
    selection_rng_state = random.getstate()
    baseline_choice = random.choice(tied)
    chosen = apply_original_editorial_selection(
        quote_choice,
        baseline_choice,
        production_candidates,
        selection_phase=selection_phase,
    )

    log.info(
        "Selected matched image basename=%s image_no=%d score=%.2f components=%s",
        chosen["basename"],
        chosen["image_no"],
        chosen["score"],
        concise_components(chosen["components"]),
    )
    log_regular_image_selection(chosen)
    log_original_editorial_shadow_result(
        quote_choice,
        baseline_choice,
        scored,
        selection_phase=selection_phase,
    )
    if generated_identity_policy_scoring_active():
        assert policy_rows is not None
        log_generated_identity_policy_applied_result(
            generated_identity_policy_applied_result(
                quote_choice,
                baseline_choice,
                scored,
                policy_rows,
                len(tied),
                selection_phase=selection_phase,
                selection_rng_state=selection_rng_state,
            )
        )
    else:
        log_generated_identity_policy_shadow_result(
            quote_choice,
            chosen,
            scored,
            selection_phase=selection_phase,
            selection_rng_state=selection_rng_state,
        )
    for item in sorted(scored, key=lambda entry: float(entry["score"]), reverse=True)[:5]:
        log.debug(
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
    log_generated_image_spacing_status: Callable,
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
    generated_images_allowed = log_generated_image_spacing_status(state)

    while attempts < max_quote_image_pair_attempts:
        attempts += 1
        try:
            quote_choice = choose_unused_line_candidate(lines_used, excluded_quote_hashes=attempted_quote_hashes)
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
                generated_images_allowed=generated_images_allowed,
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


def choose_engagement_question_image(
    images_used: set,
    quote_choice: dict,
    state: dict,
    *,
    log_generated_image_spacing_status: Callable,
    choose_matched_unused_image: Callable,
    QuoteSpecificImageMismatch: type[Exception],
    log: Logger,
) -> dict:
    """Apply the existing image policy to one fixed experimental quotation."""

    original_images_used = set(images_used)
    generated_images_allowed = log_generated_image_spacing_status(state)
    try:
        return choose_matched_unused_image(
            images_used,
            quote_choice,
            state,
            force_cycle_reset=False,
            avoid_last_image_at_cycle_boundary=True,
            cycle_boundary_exclusions=None,
            generated_images_allowed=generated_images_allowed,
            selection_phase="normal",
        )
    except QuoteSpecificImageMismatch:
        # A fixed planned member cannot follow the ordinary selector's next
        # step of trying another quotation.  Preserve the current cycle and
        # defer this member instead of manufacturing exhaustion and reusing an
        # already-consumed image.
        images_used.clear()
        images_used.update(original_images_used)
        log.warning(
            "Experimental quotation quote_hash=%s has no valid image in the "
            "current ordinary image cycle",
            quote_choice.get("quote_hash"),
        )
        raise

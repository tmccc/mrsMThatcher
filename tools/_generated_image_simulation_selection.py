"""Historical mixed-image pool and spacing rules for offline simulations only.

The required helpers moved from mrs_bot_image_selection and
mrs_bot_asset_metadata at d160dca3. Production owns its original-image selector
separately; this module preserves the retired generated-image experiments.
"""
from __future__ import annotations

import random
from collections.abc import Callable
from logging import Logger
from pathlib import Path


def merge_image_analysis(
    primary: dict,
    generated: dict | None,
    *,
    log: Logger,
) -> dict:
    """Merge image analysis."""
    if not isinstance(generated, dict):
        return primary

    merged = dict(primary)
    merged_path_index = dict(primary.get("path_index") or {})
    merged_items = dict(primary.get("items") or {})
    primary_paths = set(merged_path_index)

    for basename, image_hash in sorted((generated.get("path_index") or {}).items()):
        basename = str(basename)
        image_hash = str(image_hash)
        if basename in primary_paths:
            log.warning("Skipping generated image metadata with basename collision: %s", basename)
            continue
        item = (generated.get("items") or {}).get(image_hash)
        if not isinstance(item, dict):
            log.warning("Skipping generated image metadata with missing item hash=%s basename=%s", image_hash, basename)
            continue
        merged_path_index[basename] = image_hash
        merged_items.setdefault(image_hash, item)

    merged["path_index"] = merged_path_index
    merged["items"] = merged_items
    return merged


def load_image_analysis(
    *,
    image_analysis_file: Path,
    pool_enabled: bool,
    generated_image_analysis_file: str | Path,
    load_image_analysis_file: Callable[..., dict | None],
    merge_image_analysis: Callable[[dict, dict | None], dict],
    log: Logger,
) -> dict | None:
    """Load original and, when enabled, generated image metadata."""
    primary = load_image_analysis_file(image_analysis_file, label="image analysis")
    if primary is None or not pool_enabled:
        return primary

    generated_path = Path(str(generated_image_analysis_file)).expanduser()
    generated = load_image_analysis_file(generated_path, label="generated image analysis")
    if generated is None:
        log.warning("Generated image pool enabled but generated image analysis is unavailable; using original image pool only")
        return primary

    return merge_image_analysis(primary, generated)


def current_image_paths(
    *,
    image_glob: str,
    pool_enabled: bool,
    generated_image_dir: str | Path,
    generated_image_glob: str,
    glob: Callable[[str], list[str]],
    configured_generated_image_paths: Callable[[], dict[str, Path]],
    log: Logger,
) -> list[str]:
    """Return the current image paths."""
    images = glob(image_glob)
    images.sort()
    result = [path for path in images if Path(path).is_file()]
    if not pool_enabled:
        return result

    generated_dir = Path(str(generated_image_dir)).expanduser()
    generated_glob = str(generated_dir / str(generated_image_glob))
    try:
        configured_generated = configured_generated_image_paths()
    except ValueError as exc:
        raise RuntimeError(
            "Generated image pool contains an unsafe or unclassifiable file; "
            "refusing to select from the pool"
        ) from exc
    generated_images = [str(path) for path in configured_generated.values()]
    if not generated_images:
        log.warning("Generated image pool enabled but no generated images found matching %s", generated_glob)
        return result

    seen_basenames = {Path(path).name for path in result}
    for path in generated_images:
        basename = Path(path).name
        if basename in seen_basenames:
            log.warning("Skipping generated image with basename collision: %s path=%s", basename, path)
            continue
        result.append(path)
        seen_basenames.add(basename)
    return result


def configured_generated_image_paths(
    *,
    generated_image_dir: str | Path,
    generated_image_glob: str,
    glob: Callable[[str], list[str]],
    path_is_same_or_child: Callable[[Path, Path], bool],
    generated_image_origin_quote_hash: Callable[[str], str | None],
) -> dict[str, Path]:
    """Return the configured generated image paths."""
    generated_dir = Path(str(generated_image_dir)).expanduser()
    generated_glob = str(generated_dir / str(generated_image_glob))
    result: dict[str, Path] = {}
    for path_text in sorted(glob(generated_glob)):
        path = Path(path_text)
        if not path.is_file() or not path_is_same_or_child(path, generated_dir):
            continue
        basename = path.name
        if not generated_image_origin_quote_hash(basename):
            raise ValueError(f"invalid generated image basename in configured pool: {basename}")
        if basename in result:
            raise ValueError(f"duplicate generated image basename in configured pool: {basename}")
        result[basename] = path
    return result


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

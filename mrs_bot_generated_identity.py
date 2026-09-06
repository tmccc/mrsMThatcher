"""Generated-image identity audit validation, policy scoring and diagnostics.

The bot supplies current settings, schema/policy sets, cache, logger and sibling
helpers on each call. Only explicit loader calls read the audit and discover/hash
images. Counterfactual choices use private RNGs; logging retains the existing
shadow failure boundary. Importing this module does no runtime work and retains
no callbacks or cache.
"""

from __future__ import annotations

import json
import math
import random
from collections import Counter
from collections.abc import Callable
from logging import Logger
from pathlib import Path


def generated_identity_numeric(value: object, *, key: str, maximum: float = 10.0) -> float:
    """Return the generated identity numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= maximum:
        raise ValueError(f"{key} must be finite in 0..{maximum:g}")
    return number


def validate_generated_identity_audit_item(
    basename: str,
    item: object,
    image_by_name: dict[str, Path],
    *,
    generated_image_origin_quote_hash: Callable[[str], str | None],
    file_sha256: Callable[[Path], str],
    policies: set[str],
    dependence_values: set[str],
    generated_identity_numeric: Callable[..., float],
) -> dict:
    """Validate generated identity audit item."""
    if not generated_image_origin_quote_hash(basename):
        raise ValueError(f"non-generated basename in identity audit: {basename}")
    if basename not in image_by_name:
        raise ValueError(f"identity audit image is absent from configured generated pool: {basename}")
    if not isinstance(item, dict):
        raise ValueError(f"identity audit item for {basename} must be an object")
    if str(item.get("basename") or basename) != basename:
        raise ValueError(f"identity audit basename mismatch for {basename}")
    expected_hash = str(item.get("image_sha256") or "")
    if not expected_hash or file_sha256(image_by_name[basename]) != expected_hash:
        raise ValueError(f"stale identity audit SHA-256 for {basename}")
    origin_hash = generated_image_origin_quote_hash(basename)
    if str(item.get("origin_quote_hash") or "").lower() != origin_hash:
        raise ValueError(f"identity audit origin quote hash mismatch for {basename}")
    analysis = item.get("analysis")
    if not isinstance(analysis, dict):
        raise ValueError(f"identity audit analysis missing for {basename}")
    policy = analysis.get("recommended_cross_quote_policy")
    if policy not in policies:
        raise ValueError(f"invalid generated identity policy for {basename}: {policy!r}")
    dependence = analysis.get("identity_dependence")
    if dependence not in dependence_values:
        raise ValueError(f"invalid identity dependence for {basename}: {dependence!r}")
    if type(analysis.get("contains_specific_intended_person")) is not bool:
        raise ValueError(f"contains_specific_intended_person for {basename} must be boolean")
    for key in (
        "recognisability_to_typical_viewer",
        "recognisability_to_politically_interested_viewer",
        "meaning_retention_without_identity",
        "origin_quote_suitability",
        "recommended_penalty_strength",
    ):
        generated_identity_numeric(analysis.get(key), key=f"{basename}.{key}")
    generated_identity_numeric(analysis.get("confidence"), key=f"{basename}.confidence", maximum=1.0)
    return analysis


def load_generated_identity_audit(
    *,
    audit_file: str | Path,
    audit_cache: dict[str, dict],
    schema_version: int,
    audit_kind: str,
    configured_generated_image_paths: Callable[[], dict[str, Path]],
    validate_generated_identity_audit_item: Callable[[str, object, dict[str, Path]], dict],
) -> dict[str, dict]:
    """Load generated identity audit."""
    path = Path(str(audit_file)).expanduser()
    cache_key = str(path)
    if cache_key in audit_cache:
        return audit_cache[cache_key]
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Generated identity audit is not a JSON object: {path}")
    if (type(payload.get("schema_version")) is not int
            or payload.get("schema_version") != schema_version):
        raise RuntimeError(f"Generated identity audit has unsupported schema_version={payload.get('schema_version')!r}")
    if payload.get("analysis_kind") != audit_kind:
        raise RuntimeError(f"Generated identity audit has unexpected analysis_kind={payload.get('analysis_kind')!r}")
    items = payload.get("items")
    if not isinstance(items, dict):
        raise RuntimeError("Generated identity audit items must be an object")
    image_by_name = configured_generated_image_paths()
    if not image_by_name:
        raise RuntimeError("No configured generated images are available for identity audit validation")
    result: dict[str, dict] = {}
    try:
        for basename, item in items.items():
            basename = str(basename)
            result[basename] = validate_generated_identity_audit_item(basename, item, image_by_name)
        missing = sorted(set(image_by_name) - set(result))
        unexpected = sorted(set(result) - set(image_by_name))
        if missing or unexpected:
            raise ValueError(f"identity audit pool coverage mismatch missing={missing[:8]} unexpected={unexpected[:8]}")
    except ValueError as exc:
        raise RuntimeError(f"Invalid generated identity audit {path}: {exc}") from exc
    audit_cache[cache_key] = result
    return result


def validate_generated_identity_shadow_startup(
    *,
    pool_enabled: bool,
    shadow_enabled: bool,
    scoring_enabled: bool,
    load_generated_identity_audit: Callable[[], dict[str, dict]],
    audit_file: str | Path,
    small_penalty: float,
    strong_penalty: float,
    log: Logger,
) -> None:
    """Validate generated identity shadow startup."""
    if not pool_enabled:
        if shadow_enabled or scoring_enabled:
            log.info(
                "Generated identity-policy processing suspended because the generated image pool is disabled"
            )
        return
    if not (
        shadow_enabled
        or scoring_enabled
    ):
        return
    items = load_generated_identity_audit()
    policies = Counter(str(item.get("recommended_cross_quote_policy")) for item in items.values())
    if shadow_enabled:
        log.info(
            "Generated identity-policy shadow scoring enabled. audit_file=%s items=%d policies=%s small_penalty=%s strong_penalty=%s",
            audit_file,
            len(items),
            dict(sorted(policies.items())),
            small_penalty,
            strong_penalty,
        )
    if scoring_enabled:
        log.info(
            "Generated identity policy production scoring enabled. audit_file=%s items=%d policies=%s small_penalty=%s strong_penalty=%s",
            audit_file,
            len(items),
            dict(sorted(policies.items())),
            small_penalty,
            strong_penalty,
        )


def generated_identity_candidate_shadow_row(
    candidate: dict,
    audit_by_basename: dict[str, dict],
    *,
    small_penalty: float,
    strong_penalty: float,
) -> dict:
    """Return the generated identity candidate shadow row."""
    basename = str(candidate.get("basename") or "")
    source = str(candidate.get("image_source") or "original")
    baseline = float(candidate.get("score") or 0.0)
    origin_match = bool(candidate.get("origin_quote_match"))
    policy = None
    action = "original_unchanged"
    adjustment: float | None = 0.0
    shadow_score: float | None = baseline
    if source == "generated":
        audit = audit_by_basename.get(basename)
        if not audit:
            raise RuntimeError(f"Generated identity audit missing selected candidate: {basename}")
        policy = str(audit.get("recommended_cross_quote_policy"))
        if origin_match:
            action = "generated_origin_quote_unrestricted"
        elif policy == "unrestricted":
            action = "generated_cross_quote_unrestricted"
        elif policy == "small_penalty":
            action = "generated_cross_quote_small_penalty"
            adjustment = -float(small_penalty)
            shadow_score = baseline + adjustment
        elif policy == "strong_penalty":
            action = "generated_cross_quote_strong_penalty"
            adjustment = -float(strong_penalty)
            shadow_score = baseline + adjustment
        elif policy == "origin_quote_only":
            action = "generated_cross_quote_origin_only_excluded"
            adjustment = None
            shadow_score = None
        else:
            raise RuntimeError(f"Unsupported generated identity policy for {basename}: {policy!r}")
    return {
        "basename": basename,
        "source": source,
        "baseline_score": baseline,
        "origin_quote_match": origin_match,
        "identity_policy": policy,
        "identity_action": action,
        "identity_adjustment": adjustment,
        "identity_shadow_score": shadow_score,
    }


def generated_identity_policy_shadow_result(
    quote_choice: dict,
    production_choice: dict,
    scored_candidates: list[dict],
    *,
    selection_phase: str,
    audit_by_basename: dict[str, dict] | None = None,
    selection_rng_state: object | None = None,
    load_generated_identity_audit: Callable[[], dict[str, dict]],
    generated_identity_candidate_shadow_row: Callable[[dict, dict[str, dict]], dict],
    _choice_with_random_state: Callable[[list[dict], object], dict],
    small_penalty: float,
    strong_penalty: float,
) -> dict:
    """Evaluate generated-image identity policy without changing selection."""
    audit_by_basename = load_generated_identity_audit() if audit_by_basename is None else audit_by_basename
    rows = [generated_identity_candidate_shadow_row(candidate, audit_by_basename) for candidate in scored_candidates]
    baseline_maximum = max((float(row["baseline_score"]) for row in rows), default=None)
    baseline_tied = [
        row for row in rows if baseline_maximum is not None and float(row["baseline_score"]) == baseline_maximum
    ]
    eligible = [row for row in rows if row["identity_shadow_score"] is not None]
    maximum = max((float(row["identity_shadow_score"]) for row in eligible), default=None)
    tied = [row for row in eligible if float(row["identity_shadow_score"]) == maximum] if maximum is not None else []
    production_basename = str(production_choice.get("basename") or "")
    production_row = next(row for row in rows if row["basename"] == production_basename)
    baseline_winner = None
    shadow_winner = None
    if selection_rng_state is not None:
        baseline_winner = _choice_with_random_state(baseline_tied, selection_rng_state)
        shadow_winner = _choice_with_random_state(tied, selection_rng_state) if tied else None
    generated_rows = [row for row in rows if row["source"] == "generated"]
    cross_quote = [row for row in generated_rows if not row["origin_quote_match"]]
    excluded = [row["basename"] for row in cross_quote if row["identity_policy"] == "origin_quote_only"]
    penalised = [row["basename"] for row in cross_quote if row["identity_policy"] in {"small_penalty", "strong_penalty"}]
    policy_relevant = bool(excluded or penalised)
    counterfactual_valid = bool(
        baseline_winner is not None
        and baseline_winner["basename"] == production_basename
    )
    winner_changed = bool(
        counterfactual_valid
        and policy_relevant
        and (shadow_winner is None or shadow_winner["basename"] != baseline_winner["basename"])
    )
    if selection_rng_state is None or not counterfactual_valid:
        policy_effect = "counterfactual_mismatch"
    elif winner_changed:
        policy_effect = "winner_changed"
    elif policy_relevant:
        policy_effect = "scores_or_eligibility_only"
    else:
        policy_effect = "none"

    def winner_value(key: str) -> object:
        return shadow_winner.get(key) if shadow_winner else None
    return {
        "quote_hash": str(quote_choice.get("quote_hash") or ""),
        "line_no": int(quote_choice.get("line_no", -1)),
        "selection_phase": selection_phase,
        "production_source": production_row["source"],
        "production_winner": production_basename,
        "production_score": round(float(production_row["baseline_score"]), 4),
        "production_origin_quote_match": production_row["origin_quote_match"],
        "production_identity_policy": production_row["identity_policy"],
        "production_identity_action": production_row["identity_action"],
        "production_identity_adjustment": production_row["identity_adjustment"],
        "production_identity_shadow_score": round(float(production_row["identity_shadow_score"]), 4) if production_row["identity_shadow_score"] is not None else None,
        "baseline_winner": baseline_winner.get("basename") if baseline_winner else None,
        "baseline_tie_count": len(baseline_tied),
        "shadow_winner_source": winner_value("source"),
        "shadow_winner": winner_value("basename"),
        "shadow_winner_baseline_score": round(float(winner_value("baseline_score")), 4) if shadow_winner else None,
        "shadow_winner_origin_quote_match": winner_value("origin_quote_match"),
        "shadow_winner_identity_policy": winner_value("identity_policy"),
        "shadow_winner_identity_action": winner_value("identity_action"),
        "shadow_winner_identity_adjustment": winner_value("identity_adjustment"),
        "shadow_winner_score": round(float(winner_value("identity_shadow_score")), 4) if shadow_winner else None,
        "counterfactual_comparison_version": "generated_identity_counterfactual_v1",
        "counterfactual_comparison_valid": counterfactual_valid,
        "counterfactual_policy_winner": winner_value("basename"),
        "policy_effect": policy_effect,
        "winner_changed_by_policy": winner_changed,
        # Compatibility alias retained for existing shadow-log consumers.
        "winner_changed": winner_changed,
        "eligible_candidate_count": len(rows),
        "eligible_original_count": sum(row["source"] == "original" for row in rows),
        "eligible_generated_count": len(generated_rows),
        "cross_quote_generated_count": len(cross_quote),
        "origin_quote_generated_count": len(generated_rows) - len(cross_quote),
        "unrestricted_cross_quote_count": sum(row["identity_policy"] == "unrestricted" for row in cross_quote),
        "small_penalty_count": sum(row["identity_policy"] == "small_penalty" for row in cross_quote),
        "strong_penalty_count": sum(row["identity_policy"] == "strong_penalty" for row in cross_quote),
        "origin_quote_only_excluded_count": len(excluded),
        "excluded_generated_basenames": sorted(excluded)[:12],
        "excluded_generated_basenames_truncated": len(excluded) > 12,
        "penalised_generated_basenames": sorted(penalised)[:12],
        "penalised_generated_basenames_truncated": len(penalised) > 12,
        "small_penalty": float(small_penalty),
        "strong_penalty": float(strong_penalty),
        "shadow_tie_count": len(tied),
        "shadow_tie_handling": "shared_random_state_counterfactual",
    }


def generated_identity_policy_selection(
    scored_candidates: list[dict],
    *,
    audit_by_basename: dict[str, dict] | None = None,
    load_generated_identity_audit: Callable[[], dict[str, dict]],
    generated_identity_candidate_shadow_row: Callable[[dict, dict[str, dict]], dict],
) -> tuple[list[dict], list[dict]]:
    """Return policy rows and eligible candidates without mutating input rows."""
    audit_by_basename = load_generated_identity_audit() if audit_by_basename is None else audit_by_basename
    rows = [generated_identity_candidate_shadow_row(candidate, audit_by_basename) for candidate in scored_candidates]
    candidates_by_name = {str(candidate["basename"]): candidate for candidate in scored_candidates}
    eligible: list[dict] = []
    for row in rows:
        if row["identity_shadow_score"] is None:
            continue
        candidate = dict(candidates_by_name[row["basename"]])
        candidate["baseline_score"] = float(row["baseline_score"])
        candidate["score"] = float(row["identity_shadow_score"])
        candidate["identity_policy"] = row["identity_policy"]
        candidate["identity_action"] = row["identity_action"]
        candidate["identity_adjustment"] = row["identity_adjustment"]
        eligible.append(candidate)
    return rows, eligible


def _choice_with_random_state(candidates: list[dict], rng_state: object) -> dict:
    """Replay random.choice without consuming or changing the production RNG."""
    chooser = random.Random()
    chooser.setstate(rng_state)
    return chooser.choice(candidates)


def generated_identity_policy_applied_result(
    quote_choice: dict,
    production_winner: dict,
    baseline_candidates: list[dict],
    policy_rows: list[dict],
    policy_tie_count: int,
    *,
    selection_phase: str,
    selection_rng_state: object,
    _choice_with_random_state: Callable[[list[dict], object], dict],
) -> dict:
    """Apply the enabled generated-image identity policy to scored candidates."""
    rows_by_name = {row["basename"]: row for row in policy_rows}
    production_row = rows_by_name[str(production_winner["basename"])]
    baseline_best = max(float(row["baseline_score"]) for row in policy_rows)
    baseline_tied = [row for row in policy_rows if float(row["baseline_score"]) == baseline_best]
    policy_eligible = [row for row in policy_rows if row["identity_shadow_score"] is not None]
    policy_best = max(float(row["identity_shadow_score"]) for row in policy_eligible)
    policy_tied = [row for row in policy_eligible if float(row["identity_shadow_score"]) == policy_best]
    baseline_row = _choice_with_random_state(baseline_tied, selection_rng_state)
    counterfactual_policy_row = _choice_with_random_state(policy_tied, selection_rng_state)
    generated_rows = [row for row in policy_rows if row["source"] == "generated"]
    cross_quote = [row for row in generated_rows if not row["origin_quote_match"]]
    excluded = sorted(row["basename"] for row in cross_quote if row["identity_policy"] == "origin_quote_only")
    penalised = sorted(row["basename"] for row in cross_quote if row["identity_policy"] in {"small_penalty", "strong_penalty"})
    policy_relevant = any(
        float(row.get("identity_adjustment") or 0) != 0.0
        or row.get("identity_shadow_score") is None
        for row in cross_quote
    )
    counterfactual_valid = (
        counterfactual_policy_row["basename"] == production_row["basename"]
        and len(policy_tied) == int(policy_tie_count)
    )
    baseline_differs = baseline_row["basename"] != counterfactual_policy_row["basename"]
    changed = counterfactual_valid and policy_relevant and baseline_differs
    if not counterfactual_valid:
        policy_effect = "counterfactual_mismatch"
    elif changed:
        policy_effect = "winner_changed"
    elif policy_relevant:
        policy_effect = "scores_or_eligibility_only"
    else:
        policy_effect = "none"
    return {
        "quote_hash": str(quote_choice.get("quote_hash") or ""),
        "line_no": int(quote_choice.get("line_no", -1)),
        "selection_phase": selection_phase,
        "baseline_winner": baseline_row["basename"],
        "baseline_winner_source": baseline_row["source"],
        "baseline_winner_score": round(float(baseline_row["baseline_score"]), 4),
        "baseline_origin_quote_match": baseline_row["origin_quote_match"],
        "baseline_identity_policy": baseline_row["identity_policy"],
        "baseline_identity_action": baseline_row["identity_action"],
        "production_winner": production_row["basename"],
        "production_winner_source": production_row["source"],
        "production_policy_score": round(float(production_winner["score"]), 4),
        "production_baseline_score": round(float(production_row["baseline_score"]), 4),
        "production_origin_quote_match": production_row["origin_quote_match"],
        "production_identity_policy": production_row["identity_policy"],
        "production_identity_action": production_row["identity_action"],
        "production_identity_adjustment": production_row["identity_adjustment"],
        "counterfactual_comparison_version": "generated_identity_counterfactual_v1",
        "counterfactual_comparison_valid": counterfactual_valid,
        "counterfactual_policy_winner": counterfactual_policy_row["basename"],
        "policy_effect": policy_effect,
        "winner_changed_by_policy": changed,
        "baseline_winner_differs": baseline_differs,
        "eligible_candidate_count_before_policy": len(baseline_candidates),
        "eligible_original_count_before_policy": sum(row["source"] == "original" for row in policy_rows),
        "eligible_generated_count_before_policy": len(generated_rows),
        "eligible_candidate_count_after_policy": sum(row["identity_shadow_score"] is not None for row in policy_rows),
        "origin_quote_only_excluded_count": len(excluded),
        "small_penalty_count": sum(row["identity_policy"] == "small_penalty" for row in cross_quote),
        "strong_penalty_count": sum(row["identity_policy"] == "strong_penalty" for row in cross_quote),
        "excluded_generated_basenames": excluded[:12],
        "excluded_generated_basenames_truncated": len(excluded) > 12,
        "penalised_generated_basenames": penalised[:12],
        "penalised_generated_basenames_truncated": len(penalised) > 12,
        "replacement_source_transition": f"{baseline_row['source']}->{production_row['source']}" if changed else "unchanged",
        "policy_tie_count": int(policy_tie_count),
        "baseline_tie_count": len(baseline_tied),
        "baseline_tie_handling": "shared_random_state_counterfactual",
        "recovery_effect": selection_phase if selection_phase != "normal" else "none",
    }


def log_generated_identity_policy_applied_result(
    payload: dict,
    *,
    log: Logger,
) -> None:
    """Log generated identity policy applied result."""
    log.info("GENERATED_IDENTITY_POLICY_APPLIED %s", json.dumps(payload, sort_keys=True, separators=(",", ":")))


def log_generated_identity_policy_shadow_result(
    quote_choice: dict,
    production_choice: dict,
    scored_candidates: list[dict],
    *,
    selection_phase: str,
    selection_rng_state: object | None = None,
    generated_identity_policy_shadow_active: Callable[[], bool],
    generated_identity_policy_shadow_result: Callable[..., dict],
    log: Logger,
) -> None:
    """Log generated identity policy shadow result."""
    if not generated_identity_policy_shadow_active():
        return
    try:
        payload = generated_identity_policy_shadow_result(
            quote_choice,
            production_choice,
            scored_candidates,
            selection_phase=selection_phase,
            selection_rng_state=selection_rng_state,
        )
        log.info("GENERATED_IDENTITY_POLICY_SHADOW_RESULT %s", json.dumps(payload, sort_keys=True, separators=(",", ":")))
    except Exception:
        log.exception(
            "Generated identity-policy shadow evaluation failed; production selection remains unchanged. line_no=%s image=%s phase=%s",
            quote_choice.get("line_no"),
            production_choice.get("basename"),
            selection_phase,
        )

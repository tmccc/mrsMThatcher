#!/usr/bin/env python3
"""Guarded production policy for original-image editorial selection.

This module is deliberately local and narrow.  It contains no provider or
network integration and never selects quotations or consumes randomness.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence


POLICY_SCHEMA_VERSION = 1
SCORER_VERSION = "original-editorial-scorer-v1"
MODES = frozenset({"disabled", "shadow", "production"})
QUOTE_CLASSIFICATIONS = frozenset(
    {
        "editorial_eligible",
        "baseline_only_historically_specific",
        "baseline_only_unreviewed",
    }
)
REQUIRED_INPUT_SHA256_KEYS = frozenset(
    {
        "quotation_corpus",
        "quote_analysis",
        "quote_analysis_overrides",
        "image_analysis",
        "original_editorial_analysis",
        "evaluation_manifest",
        "gpt_reviewer_results",
        "grok_reviewer_results",
        "unblinding_mapping",
        "historical_safety_results",
        "near_duplicate_input",
    }
)
RUNTIME_INPUT_SHA256_KEYS = frozenset(
    {
        "quotation_corpus",
        "quote_analysis",
        "quote_analysis_overrides",
        "image_analysis",
        "original_editorial_analysis",
    }
)
POLICY_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "policy_id",
        "authorised_for_production",
        "scorer_version",
        "editorial_weight",
        "maximum_abs_adjustment",
        "minimum_policy_margin",
        "maximum_baseline_score_loss",
        "minimum_confirmed_post_gap",
        "input_sha256",
        "quote_policy",
        "blocked_promotion_image_sha256",
        "near_duplicate_clusters",
        "calibration",
        "generated_at",
    }
)
POLICY_CALIBRATION_KEYS = frozenset(
    {
        "status",
        "selection_rule",
        "split_algorithm",
        "split_seed",
        "bootstrap_seed",
        "bootstrap_resamples",
        "training",
        "cross_validation",
        "holdout",
        "authorisation_gates",
        "regression_fixture_sha256",
        "command",
    }
)
DECISION_ACTIONS = frozenset({"accept_promotion", "retain_baseline"})
EXPECTED_REASONS = frozenset(
    {
        "mode_disabled",
        "mode_shadow",
        "circuit_breaker_open",
        "policy_unavailable",
        "policy_invalid",
        "policy_stale",
        "policy_unauthorised",
        "generated_baseline",
        "historically_specific_quote",
        "unreviewed_quote",
        "quote_hash_mismatch",
        "no_valid_editorial_challenger",
        "baseline_already_best",
        "blocked_promotion_image",
        "recent_confirmed_image",
        "near_duplicate",
        "insufficient_policy_margin",
        "excessive_baseline_score_loss",
        "combined_score_tie",
        "ambiguous_best_challenger",
        "accepted_editorial_promotion",
    }
)
INTEGRITY_REASONS = frozenset(
    {
        "runtime_parameter_mismatch",
        "non_finite_score",
        "baseline_not_in_candidates",
        "selected_not_in_candidates",
        "candidate_content_hash_mismatch",
        "candidate_identity_collision",
        "editorial_metadata_changed",
        "malformed_policy_runtime_data",
        "impossible_score_ordering",
        "receipt_decision_inconsistency",
        "recent_history_invalid",
        "unexpected_policy_exception",
    }
)
DECISION_REASONS = EXPECTED_REASONS | INTEGRITY_REASONS
HASH_RE = re.compile(r"[0-9a-f]{64}")
DECISION_EPSILON = 1e-9
MAXIMUM_CONFIRMED_POST_GAP = 64


class EditorialPolicyError(RuntimeError):
    """A production policy is malformed, stale, or cannot be trusted."""

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        policy_id: str | None = None,
        policy_sha256: str | None = None,
    ):
        """Build an error with one bounded fail-to-baseline reason."""
        if reason not in {
            "policy_unavailable",
            "policy_invalid",
            "policy_stale",
            "policy_unauthorised",
        }:
            raise ValueError(f"unsupported policy failure reason: {reason}")
        super().__init__(message)
        self.reason = reason
        self.policy_id = policy_id
        self.policy_sha256 = policy_sha256


class EditorialDecisionIntegrityError(RuntimeError):
    """An integrity invariant failed while evaluating one decision."""

    def __init__(self, reason: str, message: str):
        """Build an error with one bounded circuit-breaker reason."""
        if reason not in INTEGRITY_REASONS:
            raise ValueError(f"unsupported editorial integrity reason: {reason}")
        super().__init__(message)
        self.reason = reason


def _duplicate_rejecting_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object name: {key}")
        result[key] = value
    return result


def strict_json_loads(document: bytes | str, *, label: str) -> object:
    """Decode strict JSON, rejecting duplicate keys and non-finite values."""
    if isinstance(document, bytes):
        document = document.decode("utf-8", errors="strict")
    if type(document) is not str:
        raise ValueError(f"{label} must be UTF-8 JSON")

    def reject_constant(value: str) -> object:
        raise ValueError(f"{label} contains non-finite JSON constant {value}")

    return json.loads(
        document,
        object_pairs_hook=_duplicate_rejecting_object,
        parse_constant=reject_constant,
    )


def canonical_json_bytes(value: object) -> bytes:
    """Return deterministic UTF-8 JSON bytes for a JSON-compatible value."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    """Return the canonical JSON SHA-256 for one value."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    """Hash one file without retaining its contents."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quote_text_sha256(text: object) -> str:
    """Match the bot's canonical quotation-content hash."""
    collapsed = re.sub(r"\s+", " ", str(text or "").strip())
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()


def _valid_hash(value: object) -> bool:
    return type(value) is str and HASH_RE.fullmatch(value) is not None


def _finite_number(value: object, *, name: str, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, not a boolean")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if number < minimum:
        raise ValueError(f"{name} must be at least {minimum:g}")
    return number


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


@dataclass(frozen=True)
class ValidatedEditorialPolicy:
    """Fully validated and immutable production policy snapshot."""

    schema_version: int
    policy_id: str
    authorised_for_production: bool
    scorer_version: str
    editorial_weight: float
    maximum_abs_adjustment: float
    minimum_policy_margin: float
    maximum_baseline_score_loss: float
    minimum_confirmed_post_gap: int
    input_sha256: Mapping[str, str]
    quote_policy: Mapping[str, str]
    blocked_promotion_image_sha256: frozenset[str]
    near_duplicate_clusters: tuple[frozenset[str], ...]
    calibration: Mapping[str, object]
    generated_at: str
    policy_sha256: str
    image_sha256_by_basename: Mapping[str, str]
    raw_policy: Mapping[str, object] = field(repr=False)

    def same_near_duplicate_cluster(self, left: str, right: str) -> bool:
        """Return whether two hashes share one validated strict cluster."""
        return any(left in cluster and right in cluster for cluster in self.near_duplicate_clusters)


def validate_policy_document(
    data: object,
    *,
    runtime_weight: float,
    runtime_maximum_abs_adjustment: float,
    runtime_input_sha256: Mapping[str, str],
    current_original_sha256_by_basename: Mapping[str, str],
    current_quote_hashes: Sequence[str],
) -> ValidatedEditorialPolicy:
    """Validate and freeze one policy document against current runtime inputs."""
    try:
        if not isinstance(data, dict) or set(data) != POLICY_TOP_LEVEL_KEYS:
            raise ValueError("top-level policy fields mismatch")
        if type(data.get("schema_version")) is not int or data["schema_version"] != POLICY_SCHEMA_VERSION:
            raise ValueError("unsupported schema_version")
        policy_id = data.get("policy_id")
        if (
            type(policy_id) is not str
            or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,79}", policy_id)
        ):
            raise ValueError("policy_id must be a bounded lowercase identifier")
        if type(data.get("authorised_for_production")) is not bool:
            raise ValueError("authorised_for_production must be boolean")
        if data.get("scorer_version") != SCORER_VERSION:
            raise ValueError("scorer_version does not match the runtime scorer")

        weight = _finite_number(data.get("editorial_weight"), name="editorial_weight")
        cap = _finite_number(
            data.get("maximum_abs_adjustment"),
            name="maximum_abs_adjustment",
        )
        minimum_margin = _finite_number(
            data.get("minimum_policy_margin"),
            name="minimum_policy_margin",
        )
        maximum_loss = _finite_number(
            data.get("maximum_baseline_score_loss"),
            name="maximum_baseline_score_loss",
        )
        if weight != float(runtime_weight) or cap != float(runtime_maximum_abs_adjustment):
            raise ValueError("policy scorer weight/cap do not match runtime values")

        gap = data.get("minimum_confirmed_post_gap")
        if type(gap) is not int or not 0 <= gap <= MAXIMUM_CONFIRMED_POST_GAP:
            raise ValueError(
                f"minimum_confirmed_post_gap must be an integer in 0..{MAXIMUM_CONFIRMED_POST_GAP}"
            )

        inputs = data.get("input_sha256")
        if not isinstance(inputs, dict) or set(inputs) != REQUIRED_INPUT_SHA256_KEYS:
            raise ValueError("input_sha256 fields mismatch")
        if any(not _valid_hash(value) for value in inputs.values()):
            raise ValueError("input_sha256 values must be lowercase SHA-256 digests")
        if set(runtime_input_sha256) != RUNTIME_INPUT_SHA256_KEYS:
            raise ValueError("runtime input hash fields mismatch")
        if any(not _valid_hash(value) for value in runtime_input_sha256.values()):
            raise ValueError("runtime input hashes are malformed")
        stale = sorted(
            key
            for key in RUNTIME_INPUT_SHA256_KEYS
            if inputs[key] != runtime_input_sha256[key]
        )
        if stale:
            raise EditorialPolicyError(
                "policy_stale",
                "production policy input hash mismatch: " + ", ".join(stale),
            )

        quote_policy = data.get("quote_policy")
        if not isinstance(quote_policy, dict):
            raise ValueError("quote_policy must be an object")
        for quote_hash, classification in quote_policy.items():
            if not _valid_hash(quote_hash):
                raise ValueError("quote_policy contains malformed quote hash")
            if classification not in QUOTE_CLASSIFICATIONS:
                raise ValueError(f"invalid quote classification for {quote_hash}")
        current_quotes = list(current_quote_hashes)
        if (
            len(current_quotes) != len(set(current_quotes))
            or any(not _valid_hash(value) for value in current_quotes)
            or not set(current_quotes).issubset(set(quote_policy))
        ):
            raise ValueError(
                "every current quotation must have an explicit valid classification"
            )

        originals = dict(current_original_sha256_by_basename)
        if not originals:
            raise ValueError("current original image corpus is empty")
        if any(
            type(name) is not str
            or not name
            or Path(name).name != name
            or not _valid_hash(image_hash)
            for name, image_hash in originals.items()
        ):
            raise ValueError("current original image identity map is malformed")
        if len(set(originals.values())) != len(originals):
            raise ValueError("current original image corpus has a content identity collision")
        current_hashes = set(originals.values())

        blocked_value = data.get("blocked_promotion_image_sha256")
        if not isinstance(blocked_value, list):
            raise ValueError("blocked_promotion_image_sha256 must be an array")
        if len(blocked_value) != len(set(blocked_value)):
            raise ValueError("blocked_promotion_image_sha256 contains duplicates")
        if any(not _valid_hash(value) for value in blocked_value):
            raise ValueError("blocked promotion image hash is malformed")
        if not set(blocked_value).issubset(current_hashes):
            raise ValueError("blocked promotion image hash is absent from current originals")

        raw_clusters = data.get("near_duplicate_clusters")
        if not isinstance(raw_clusters, list):
            raise ValueError("near_duplicate_clusters must be an array")
        clusters: list[frozenset[str]] = []
        seen_cluster_hashes: set[str] = set()
        for raw_cluster in raw_clusters:
            if not isinstance(raw_cluster, list) or len(raw_cluster) < 2:
                raise ValueError("each near-duplicate cluster must contain at least two hashes")
            if len(raw_cluster) != len(set(raw_cluster)):
                raise ValueError("near-duplicate cluster contains duplicate hashes")
            if any(not _valid_hash(value) for value in raw_cluster):
                raise ValueError("near-duplicate cluster contains malformed hash")
            cluster = frozenset(raw_cluster)
            if not cluster.issubset(current_hashes):
                raise ValueError("near-duplicate cluster refers to absent current original")
            if cluster & seen_cluster_hashes:
                raise ValueError("near-duplicate image appears in more than one cluster")
            seen_cluster_hashes.update(cluster)
            clusters.append(cluster)

        calibration = data.get("calibration")
        if not isinstance(calibration, dict) or set(calibration) != POLICY_CALIBRATION_KEYS:
            raise ValueError("calibration fields mismatch")
        if calibration.get("status") not in {"authorised", "unauthorised"}:
            raise ValueError("calibration.status must be authorised or unauthorised")
        if (calibration["status"] == "authorised") != data["authorised_for_production"]:
            raise ValueError("calibration status contradicts production authorisation")
        fixture_hash = calibration.get("regression_fixture_sha256")
        if fixture_hash is not None and not _valid_hash(fixture_hash):
            raise ValueError("calibration regression fixture hash is malformed")
        for key in ("split_seed", "bootstrap_seed", "bootstrap_resamples"):
            if type(calibration.get(key)) is not int or calibration[key] < 0:
                raise ValueError(f"calibration.{key} must be a non-negative integer")
        for key in (
            "selection_rule",
            "split_algorithm",
            "command",
        ):
            if type(calibration.get(key)) is not str or not calibration[key]:
                raise ValueError(f"calibration.{key} must be a non-empty string")
        for key in ("training", "cross_validation", "holdout", "authorisation_gates"):
            if not isinstance(calibration.get(key), (dict, list)):
                raise ValueError(f"calibration.{key} must be structured data")

        generated_at = data.get("generated_at")
        if type(generated_at) is not str or not generated_at:
            raise ValueError("generated_at must be a non-empty timestamp")
        parsed_generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        if parsed_generated.tzinfo is None:
            raise ValueError("generated_at must include a timezone")
    except EditorialPolicyError:
        raise
    except (TypeError, ValueError, OverflowError) as exc:
        raise EditorialPolicyError("policy_invalid", f"invalid production policy: {exc}") from exc

    policy_hash = canonical_sha256(data)
    frozen_data = _freeze_json(copy.deepcopy(data))
    assert isinstance(frozen_data, Mapping)
    return ValidatedEditorialPolicy(
        schema_version=POLICY_SCHEMA_VERSION,
        policy_id=str(policy_id),
        authorised_for_production=bool(data["authorised_for_production"]),
        scorer_version=SCORER_VERSION,
        editorial_weight=weight,
        maximum_abs_adjustment=cap,
        minimum_policy_margin=minimum_margin,
        maximum_baseline_score_loss=maximum_loss,
        minimum_confirmed_post_gap=int(gap),
        input_sha256=MappingProxyType(dict(inputs)),
        quote_policy=MappingProxyType(dict(quote_policy)),
        blocked_promotion_image_sha256=frozenset(blocked_value),
        near_duplicate_clusters=tuple(clusters),
        calibration=MappingProxyType(dict(calibration)),
        generated_at=str(generated_at),
        policy_sha256=policy_hash,
        image_sha256_by_basename=MappingProxyType(originals),
        raw_policy=frozen_data,
    )


def load_validated_policy(
    path: Path,
    *,
    runtime_weight: float,
    runtime_maximum_abs_adjustment: float,
    runtime_input_sha256: Mapping[str, str],
    current_original_sha256_by_basename: Mapping[str, str],
    current_quote_hashes: Sequence[str],
) -> ValidatedEditorialPolicy:
    """Read, strictly validate, and freeze one production policy."""
    try:
        document = Path(path).read_bytes()
    except (OSError, ValueError) as exc:
        raise EditorialPolicyError(
            "policy_unavailable",
            f"production policy could not be read at {path}: {exc}",
        ) from exc
    try:
        data = strict_json_loads(document, label="original editorial production policy")
    except Exception as exc:
        raise EditorialPolicyError(
            "policy_invalid",
            f"production policy JSON is invalid: {exc}",
        ) from exc
    try:
        policy = validate_policy_document(
            data,
            runtime_weight=runtime_weight,
            runtime_maximum_abs_adjustment=runtime_maximum_abs_adjustment,
            runtime_input_sha256=runtime_input_sha256,
            current_original_sha256_by_basename=current_original_sha256_by_basename,
            current_quote_hashes=current_quote_hashes,
        )
    except EditorialPolicyError as exc:
        # A structurally parseable stale document still has a useful bounded
        # identity for breaker health.  Do not attach it to malformed JSON.
        if isinstance(data, dict):
            policy_id = data.get("policy_id")
            if type(policy_id) is str:
                exc.policy_id = policy_id
            try:
                exc.policy_sha256 = canonical_sha256(data)
            except (TypeError, ValueError):
                pass
        raise
    if not policy.authorised_for_production:
        raise EditorialPolicyError(
            "policy_unauthorised",
            f"production policy {policy.policy_id} is not authorised_for_production",
            policy_id=policy.policy_id,
            policy_sha256=policy.policy_sha256,
        )
    return policy


@dataclass
class EditorialCircuitBreaker:
    """One-process fail-to-baseline latch for editorial production."""

    resolved_mode: str = "disabled"
    mode_source: str = "default"
    is_open: bool = False
    first_failure_reason: str | None = None
    first_failure_time: str | None = None
    failure_count: int = 0
    policy_id: str | None = None
    policy_sha256: str | None = None

    def reset_for_process(
        self,
        *,
        resolved_mode: str,
        mode_source: str,
        policy: ValidatedEditorialPolicy | None = None,
    ) -> None:
        """Close and clear the latch for a clean process initialisation."""
        if resolved_mode not in MODES:
            raise ValueError(f"invalid editorial mode: {resolved_mode!r}")
        self.resolved_mode = resolved_mode
        self.mode_source = mode_source
        self.is_open = False
        self.first_failure_reason = None
        self.first_failure_time = None
        self.failure_count = 0
        self.policy_id = policy.policy_id if policy else None
        self.policy_sha256 = policy.policy_sha256 if policy else None

    def open(self, reason: str, *, at: datetime | None = None) -> bool:
        """Latch open and return whether this was the first failure."""
        if reason not in INTEGRITY_REASONS | {
            "policy_unavailable",
            "policy_invalid",
            "policy_stale",
            "policy_unauthorised",
        }:
            raise ValueError(f"invalid circuit-breaker reason: {reason}")
        first = not self.is_open
        self.failure_count += 1
        self.is_open = True
        if first:
            observed = at or datetime.now(timezone.utc)
            self.first_failure_reason = reason
            self.first_failure_time = observed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return first

    def snapshot(self) -> dict[str, object]:
        """Return an independent serialisable breaker status."""
        return {
            "open": self.is_open,
            "first_failure_reason": self.first_failure_reason,
            "first_failure_time": self.first_failure_time,
            "failure_count": self.failure_count,
            "resolved_mode": self.resolved_mode,
            "mode_source": self.mode_source,
            "policy_id": self.policy_id,
            "policy_sha256": self.policy_sha256,
        }


def resolve_mode(
    *,
    canonical_present: bool,
    canonical_value: object,
    legacy_present: bool,
    legacy_value: object,
) -> tuple[str, str]:
    """Resolve canonical/legacy settings with explicit contradiction rejection."""
    if legacy_present and type(legacy_value) is not bool:
        raise ValueError("ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING must be boolean")
    if canonical_present:
        if type(canonical_value) is not str or canonical_value not in MODES:
            raise ValueError(
                "ORIGINAL_EDITORIAL_MODE must be disabled, shadow, or production"
            )
        expected_legacy = canonical_value == "shadow"
        if legacy_present and (
            canonical_value == "production" or legacy_value is not expected_legacy
        ):
            raise ValueError(
                "ORIGINAL_EDITORIAL_MODE contradicts the legacy shadow boolean; "
                "remove the legacy key before selecting production"
            )
        return canonical_value, "canonical"
    if legacy_present:
        return ("shadow" if legacy_value else "disabled"), "legacy"
    return "disabled", "default"


def _score_value(candidate: Mapping[str, object]) -> float:
    value = candidate.get("score")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EditorialDecisionIntegrityError(
            "non_finite_score", "candidate score is not numeric"
        )
    number = float(value)
    if not math.isfinite(number):
        raise EditorialDecisionIntegrityError(
            "non_finite_score", "candidate score is not finite"
        )
    return number


def _blank_candidate_payload() -> dict[str, object]:
    return {
        "basename": None,
        "source": None,
        "content_sha256": None,
        "raw_score": None,
        "editorial_adjustment": None,
        "combined_score": None,
    }


def _blank_guard_payload() -> dict[str, object]:
    return {
        "quote_classification": None,
        "blocked_promotion": False,
        "recent_confirmed": False,
        "near_duplicate": False,
        "minimum_margin": False,
        "maximum_baseline_loss": False,
    }


def _candidate_payload(
    candidate: Mapping[str, object],
    *,
    raw_score: float,
    adjustment: float,
) -> dict[str, object]:
    return {
        "basename": str(candidate.get("basename") or ""),
        "source": str(candidate.get("image_source") or ""),
        "content_sha256": str(candidate.get("image_hash") or ""),
        "raw_score": raw_score,
        "editorial_adjustment": adjustment,
        "combined_score": raw_score + adjustment,
    }


def guarded_editorial_decision(
    *,
    quote: Mapping[str, object],
    candidate_rows: Sequence[dict],
    baseline_winner: dict,
    editorial_by_basename: Mapping[str, Mapping[str, object]],
    score_adjustment: Callable[[Mapping[str, object] | None, Mapping[str, object] | None], tuple[float, dict]],
    policy: ValidatedEditorialPolicy | None,
    recent_confirmed_images: Sequence[Mapping[str, object]],
    resolved_mode: str,
    circuit_breaker_open: bool,
    editorial_metadata_sha256: str | None,
    runtime_weight: float,
    runtime_maximum_abs_adjustment: float,
    selection_phase: str,
    epsilon: float = DECISION_EPSILON,
) -> tuple[dict, dict[str, object]]:
    """Return the exact authoritative object and an independent decision.

    Integrity failures raise :class:`EditorialDecisionIntegrityError`; callers
    open the process breaker and retain ``baseline_winner``.  Expected policy
    exclusions are returned normally and never mutate any supplied object.
    """
    if resolved_mode not in MODES:
        raise EditorialDecisionIntegrityError(
            "malformed_policy_runtime_data", "resolved mode is invalid"
        )
    if isinstance(epsilon, bool) or not isinstance(epsilon, (int, float)) or not math.isfinite(float(epsilon)) or epsilon <= 0:
        raise EditorialDecisionIntegrityError(
            "malformed_policy_runtime_data", "decision epsilon is invalid"
        )
    candidates = list(candidate_rows)
    if not any(candidate is baseline_winner for candidate in candidates):
        raise EditorialDecisionIntegrityError(
            "baseline_not_in_candidates", "baseline object is absent from supplied rows"
        )
    if len({id(candidate) for candidate in candidates}) != len(candidates):
        raise EditorialDecisionIntegrityError(
            "candidate_identity_collision", "the same candidate object appears more than once"
        )

    basenames: set[str] = set()
    content_hashes: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise EditorialDecisionIntegrityError(
                "candidate_identity_collision", "candidate row is not a dictionary"
            )
        basename = candidate.get("basename")
        image_hash = candidate.get("image_hash")
        if (
            type(basename) is not str
            or not basename
            or type(image_hash) is not str
            or not _valid_hash(image_hash)
        ):
            raise EditorialDecisionIntegrityError(
                "candidate_content_hash_mismatch", "candidate identity is malformed"
            )
        if basename in basenames or image_hash in content_hashes:
            raise EditorialDecisionIntegrityError(
                "candidate_identity_collision",
                "candidate basename or content identity collides",
            )
        basenames.add(basename)
        content_hashes.add(image_hash)
        _score_value(candidate)

    quote_hash = str(quote.get("quote_hash") or "")
    decision: dict[str, object] = {
        "schema_version": 1,
        "quote_hash": quote_hash,
        "selection_phase": str(selection_phase),
        "resolved_mode": resolved_mode,
        "action": "retain_baseline",
        "reason": "mode_disabled",
        "winner_changed_by_policy": False,
        "baseline": _blank_candidate_payload(),
        "challenger": _blank_candidate_payload(),
        "authoritative": {
            "basename": str(baseline_winner.get("basename") or ""),
            "source": str(baseline_winner.get("image_source") or ""),
            "content_sha256": str(baseline_winner.get("image_hash") or ""),
        },
        "policy_margin": None,
        "baseline_score_loss": None,
        "policy_id": policy.policy_id if policy else None,
        "policy_sha256": policy.policy_sha256 if policy else None,
        "editorial_metadata_sha256": editorial_metadata_sha256,
        "input_sha256": dict(policy.input_sha256) if policy else {},
        "epsilon": float(epsilon),
        "guards": _blank_guard_payload(),
    }
    baseline_raw_initial = _score_value(baseline_winner)
    if any(
        _score_value(candidate) > baseline_raw_initial + float(epsilon)
        for candidate in candidates
    ):
        raise EditorialDecisionIntegrityError(
            "impossible_score_ordering",
            "ordinary baseline is not a maximum-score final candidate",
        )
    baseline_is_original = baseline_winner.get("image_source") == "original"
    decision["baseline"] = {
        "basename": str(baseline_winner.get("basename") or ""),
        "source": str(baseline_winner.get("image_source") or ""),
        "content_sha256": str(baseline_winner.get("image_hash") or ""),
        "raw_score": baseline_raw_initial,
        # An original adjustment is intentionally unknown until a validated
        # policy and its pinned metadata are available.  Generated images are
        # outside this policy and therefore have an exact zero adjustment.
        "editorial_adjustment": None if baseline_is_original else 0.0,
        "combined_score": None if baseline_is_original else baseline_raw_initial,
    }

    def finish(reason: str, challenger: dict | None = None) -> tuple[dict, dict[str, object]]:
        if reason not in EXPECTED_REASONS:
            raise EditorialDecisionIntegrityError(
                "malformed_policy_runtime_data", f"unbounded decision reason {reason}"
            )
        decision["reason"] = reason
        selected = baseline_winner
        if reason == "accepted_editorial_promotion":
            if challenger is None or not any(candidate is challenger for candidate in candidates):
                raise EditorialDecisionIntegrityError(
                    "selected_not_in_candidates", "accepted challenger is absent from supplied rows"
                )
            selected = challenger
            decision["action"] = "accept_promotion"
            decision["winner_changed_by_policy"] = challenger is not baseline_winner
            decision["authoritative"] = {
                "basename": str(challenger["basename"]),
                "source": str(challenger["image_source"]),
                "content_sha256": str(challenger["image_hash"]),
            }
        return selected, copy.deepcopy(decision)

    if resolved_mode == "disabled":
        return finish("mode_disabled")
    if resolved_mode == "shadow":
        return finish("mode_shadow")
    if circuit_breaker_open:
        return finish("circuit_breaker_open")
    if policy is None:
        return finish("policy_unavailable")
    if not policy.authorised_for_production:
        return finish("policy_unauthorised")
    if (
        policy.scorer_version != SCORER_VERSION
        or policy.editorial_weight != float(runtime_weight)
        or policy.maximum_abs_adjustment != float(runtime_maximum_abs_adjustment)
    ):
        raise EditorialDecisionIntegrityError(
            "runtime_parameter_mismatch", "validated policy scorer parameters changed"
        )
    if editorial_metadata_sha256 != policy.input_sha256["original_editorial_analysis"]:
        raise EditorialDecisionIntegrityError(
            "editorial_metadata_changed", "editorial metadata changed after policy validation"
        )
    if baseline_winner.get("image_source") != "original":
        return finish("generated_baseline")
    if not _valid_hash(quote_hash) or quote_text_sha256(quote.get("text")) != quote_hash:
        return finish("quote_hash_mismatch")

    classification = policy.quote_policy.get(
        quote_hash, "baseline_only_unreviewed"
    )
    guards = decision["guards"]
    assert isinstance(guards, dict)
    guards["quote_classification"] = classification
    if classification == "baseline_only_historically_specific":
        return finish("historically_specific_quote")
    if classification != "editorial_eligible":
        return finish("unreviewed_quote")

    def validated_adjustment(candidate: dict) -> tuple[float, dict]:
        basename = str(candidate["basename"])
        expected_hash = policy.image_sha256_by_basename.get(basename)
        if expected_hash != candidate["image_hash"]:
            raise EditorialDecisionIntegrityError(
                "candidate_content_hash_mismatch",
                f"candidate content hash differs from validated current original: {basename}",
            )
        editorial = editorial_by_basename.get(basename)
        if not isinstance(editorial, Mapping):
            raise EditorialDecisionIntegrityError(
                "editorial_metadata_changed", f"editorial metadata missing for {basename}"
            )
        try:
            adjustment, detail = score_adjustment(quote.get("analysis"), editorial)
        except EditorialDecisionIntegrityError:
            raise
        except Exception as exc:
            raise EditorialDecisionIntegrityError(
                "unexpected_policy_exception", f"editorial scorer failed for {basename}: {exc}"
            ) from exc
        if (
            isinstance(adjustment, bool)
            or not isinstance(adjustment, (int, float))
            or not math.isfinite(float(adjustment))
            or not isinstance(detail, dict)
        ):
            raise EditorialDecisionIntegrityError(
                "non_finite_score", f"editorial adjustment is invalid for {basename}"
            )
        return float(adjustment), copy.deepcopy(detail)

    baseline_raw = baseline_raw_initial
    baseline_adjustment, _baseline_detail = validated_adjustment(baseline_winner)
    baseline_combined = baseline_raw + baseline_adjustment
    if not math.isfinite(baseline_combined):
        raise EditorialDecisionIntegrityError(
            "non_finite_score", "baseline combined score is not finite"
        )
    decision["baseline"] = _candidate_payload(
        baseline_winner,
        raw_score=baseline_raw,
        adjustment=baseline_adjustment,
    )

    challenger_rows: list[tuple[float, str, dict, float, float]] = []
    for candidate in candidates:
        if candidate is baseline_winner or candidate.get("image_source") != "original":
            continue
        basename = str(candidate["basename"])
        if basename not in editorial_by_basename:
            continue
        raw_score = _score_value(candidate)
        adjustment, _detail = validated_adjustment(candidate)
        combined = raw_score + adjustment
        if not math.isfinite(combined):
            raise EditorialDecisionIntegrityError(
                "non_finite_score", f"challenger combined score is not finite for {basename}"
            )
        challenger_rows.append((combined, basename, candidate, raw_score, adjustment))
    if not challenger_rows:
        return finish("no_valid_editorial_challenger")

    best_combined = max(row[0] for row in challenger_rows)
    best_rows = [row for row in challenger_rows if abs(row[0] - best_combined) <= float(epsilon)]
    if len(best_rows) != 1:
        return finish("ambiguous_best_challenger")
    combined, _basename, challenger, challenger_raw, challenger_adjustment = best_rows[0]
    decision["challenger"] = _candidate_payload(
        challenger,
        raw_score=challenger_raw,
        adjustment=challenger_adjustment,
    )
    policy_margin = combined - baseline_combined
    baseline_score_loss = baseline_raw - challenger_raw
    if not math.isfinite(policy_margin) or not math.isfinite(baseline_score_loss):
        raise EditorialDecisionIntegrityError(
            "non_finite_score", "derived decision scores are not finite"
        )
    decision["policy_margin"] = policy_margin
    decision["baseline_score_loss"] = baseline_score_loss

    if abs(policy_margin) <= float(epsilon):
        return finish("combined_score_tie")
    if policy_margin < 0:
        return finish("baseline_already_best")

    challenger_hash = str(challenger["image_hash"])
    baseline_hash = str(baseline_winner["image_hash"])
    if challenger_hash in policy.blocked_promotion_image_sha256:
        guards["blocked_promotion"] = True
        return finish("blocked_promotion_image")

    if not isinstance(recent_confirmed_images, Sequence) or isinstance(
        recent_confirmed_images, (str, bytes)
    ):
        raise EditorialDecisionIntegrityError(
            "recent_history_invalid", "recent confirmed-image history is not a sequence"
        )
    recent_hashes: list[str] = []
    recent_post_ids: set[str] = set()
    for record in recent_confirmed_images:
        if not isinstance(record, Mapping):
            raise EditorialDecisionIntegrityError(
                "recent_history_invalid", "recent confirmed-image record is malformed"
            )
        post_id = record.get("post_id")
        image_hash = record.get("image_sha256")
        basename = record.get("image_basename")
        if (
            type(post_id) is not str
            or not post_id
            or post_id in recent_post_ids
            or not _valid_hash(image_hash)
            or type(basename) is not str
            or not basename
            or Path(basename).name != basename
        ):
            raise EditorialDecisionIntegrityError(
                "recent_history_invalid", "recent confirmed-image identity is impossible"
            )
        recent_post_ids.add(post_id)
        recent_hashes.append(str(image_hash))
    gap = policy.minimum_confirmed_post_gap
    if gap and challenger_hash in recent_hashes[-gap:]:
        guards["recent_confirmed"] = True
        return finish("recent_confirmed_image")

    if policy.same_near_duplicate_cluster(baseline_hash, challenger_hash):
        guards["near_duplicate"] = True
        return finish("near_duplicate")
    if policy_margin + float(epsilon) < policy.minimum_policy_margin:
        guards["minimum_margin"] = True
        return finish("insufficient_policy_margin")
    if baseline_score_loss - float(epsilon) > policy.maximum_baseline_score_loss:
        guards["maximum_baseline_loss"] = True
        return finish("excessive_baseline_score_loss")
    if challenger is baseline_winner:
        return finish("baseline_already_best")
    return finish("accepted_editorial_promotion", challenger)


def decision_integrity_fallback(
    *,
    baseline_winner: dict,
    quote_hash: object,
    selection_phase: object,
    resolved_mode: str,
    reason: str,
    policy: ValidatedEditorialPolicy | None,
    editorial_metadata_sha256: str | None,
    breaker_snapshot: Mapping[str, object],
) -> tuple[dict, dict[str, object]]:
    """Build a bounded baseline decision after one caught integrity failure."""
    if reason not in INTEGRITY_REASONS:
        raise ValueError(f"unbounded integrity reason: {reason}")
    baseline_score = baseline_winner.get("score")
    if isinstance(baseline_score, bool) or not isinstance(baseline_score, (int, float)) or not math.isfinite(float(baseline_score)):
        baseline_score = None
    payload = {
        "schema_version": 1,
        "quote_hash": str(quote_hash or ""),
        "selection_phase": str(selection_phase or ""),
        "resolved_mode": resolved_mode,
        "action": "retain_baseline",
        "reason": reason,
        "winner_changed_by_policy": False,
        "baseline": {
            "basename": str(baseline_winner.get("basename") or ""),
            "source": str(baseline_winner.get("image_source") or ""),
            "content_sha256": str(baseline_winner.get("image_hash") or ""),
            "raw_score": float(baseline_score) if baseline_score is not None else None,
            "editorial_adjustment": None,
            "combined_score": None,
        },
        "challenger": _blank_candidate_payload(),
        "authoritative": {
            "basename": str(baseline_winner.get("basename") or ""),
            "source": str(baseline_winner.get("image_source") or ""),
            "content_sha256": str(baseline_winner.get("image_hash") or ""),
        },
        "policy_margin": None,
        "baseline_score_loss": None,
        "policy_id": policy.policy_id if policy else None,
        "policy_sha256": policy.policy_sha256 if policy else None,
        "editorial_metadata_sha256": editorial_metadata_sha256,
        "input_sha256": dict(policy.input_sha256) if policy else {},
        "epsilon": DECISION_EPSILON,
        "guards": _blank_guard_payload(),
        "circuit_breaker": dict(breaker_snapshot),
    }
    return baseline_winner, copy.deepcopy(payload)

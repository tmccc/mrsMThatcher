from __future__ import annotations

import copy
import json
import math
import random
from pathlib import Path

import pytest

import original_editorial_production as production


def digest(label: str) -> str:
    """Return a stable synthetic SHA-256 identity."""
    return __import__("hashlib").sha256(label.encode("utf-8")).hexdigest()


def quote(text: str = "A clear quotation") -> dict:
    """Build one hash-consistent synthetic quotation."""
    return {
        "text": text,
        "quote_hash": production.quote_text_sha256(text),
        "analysis": {"topic": "economy"},
    }


def candidate(
    name: str,
    score: float,
    *,
    source: str = "original",
    image_hash: str | None = None,
) -> dict:
    """Build one production-like eligible candidate row."""
    return {
        "basename": name,
        "image_hash": image_hash or digest(name),
        "image_source": source,
        "score": score,
        "components": {"topic": score},
        "nested": {"unchanged": [name]},
    }


def calibration(*, authorised: bool = True) -> dict:
    """Build the strict policy calibration envelope."""
    return {
        "status": "authorised" if authorised else "unauthorised",
        "selection_rule": "fixed test rule",
        "split_algorithm": "grouped by quotation hash",
        "split_seed": 17,
        "bootstrap_seed": 19,
        "bootstrap_resamples": 1000,
        "training": {},
        "cross_validation": [],
        "holdout": {},
        "authorisation_gates": {},
        "regression_fixture_sha256": None,
        "command": "offline-test",
    }


def policy_document(
    rows: list[dict],
    current_quote: dict,
    *,
    margin: float = 0.5,
    loss: float = 2.0,
    gap: int = 3,
    blocked: list[str] | None = None,
    clusters: list[list[str]] | None = None,
    classification: str = "editorial_eligible",
    authorised: bool = True,
) -> tuple[dict, dict[str, str], dict[str, str]]:
    """Build a strict policy plus its current runtime identity inputs."""
    runtime_inputs = {
        key: digest(key) for key in production.RUNTIME_INPUT_SHA256_KEYS
    }
    all_inputs = {
        key: runtime_inputs.get(key, digest(key))
        for key in production.REQUIRED_INPUT_SHA256_KEYS
    }
    originals = {
        row["basename"]: row["image_hash"]
        for row in rows
        if row["image_source"] == "original"
    }
    document = {
        "schema_version": 1,
        "policy_id": "editorial-test-v1",
        "authorised_for_production": authorised,
        "scorer_version": production.SCORER_VERSION,
        "editorial_weight": 0.32,
        "maximum_abs_adjustment": 4.0,
        "minimum_policy_margin": margin,
        "maximum_baseline_score_loss": loss,
        "minimum_confirmed_post_gap": gap,
        "input_sha256": all_inputs,
        "quote_policy": {current_quote["quote_hash"]: classification},
        "blocked_promotion_image_sha256": list(blocked or []),
        "near_duplicate_clusters": list(clusters or []),
        "calibration": calibration(authorised=authorised),
        "generated_at": "2026-09-03T00:00:00Z",
    }
    return document, runtime_inputs, originals


def validated_policy(
    rows: list[dict],
    current_quote: dict,
    **kwargs: object,
) -> production.ValidatedEditorialPolicy:
    """Validate a synthetic authorised policy."""
    document, inputs, originals = policy_document(rows, current_quote, **kwargs)
    return production.validate_policy_document(
        document,
        runtime_weight=0.32,
        runtime_maximum_abs_adjustment=4.0,
        runtime_input_sha256=inputs,
        current_original_sha256_by_basename=originals,
        current_quote_hashes=[current_quote["quote_hash"]],
    )


def adjustment(_quote: object, editorial: object) -> tuple[float, dict]:
    """Return the fixture adjustment without side effects."""
    assert isinstance(editorial, dict)
    return float(editorial["adjustment"]), {"fixture": True}


def decide(
    rows: list[dict],
    baseline: dict,
    current_quote: dict,
    *,
    policy: production.ValidatedEditorialPolicy | None = None,
    editorial: dict[str, dict] | None = None,
    recent: list[dict] | None = None,
    mode: str = "production",
    breaker: bool = False,
    weight: float = 0.32,
    cap: float = 4.0,
) -> tuple[dict, dict]:
    """Call the pure helper with standard runtime inputs."""
    return production.guarded_editorial_decision(
        quote=current_quote,
        candidate_rows=rows,
        baseline_winner=baseline,
        editorial_by_basename=editorial or {},
        score_adjustment=adjustment,
        policy=policy,
        recent_confirmed_images=recent or [],
        resolved_mode=mode,
        circuit_breaker_open=breaker,
        editorial_metadata_sha256=(
            policy.input_sha256["original_editorial_analysis"]
            if policy is not None
            else None
        ),
        runtime_weight=weight,
        runtime_maximum_abs_adjustment=cap,
        selection_phase="normal",
    )


@pytest.mark.parametrize(
    ("canonical_present", "canonical", "legacy_present", "legacy", "expected"),
    [
        (False, None, False, None, ("disabled", "default")),
        (False, None, True, False, ("disabled", "legacy")),
        (False, None, True, True, ("shadow", "legacy")),
        (True, "disabled", False, None, ("disabled", "canonical")),
        (True, "shadow", False, None, ("shadow", "canonical")),
        (True, "production", False, None, ("production", "canonical")),
        (True, "disabled", True, False, ("disabled", "canonical")),
        (True, "shadow", True, True, ("shadow", "canonical")),
    ],
)
def test_mode_resolution(
    canonical_present: bool,
    canonical: object,
    legacy_present: bool,
    legacy: object,
    expected: tuple[str, str],
) -> None:
    assert production.resolve_mode(
        canonical_present=canonical_present,
        canonical_value=canonical,
        legacy_present=legacy_present,
        legacy_value=legacy,
    ) == expected


@pytest.mark.parametrize(
    ("canonical", "legacy"),
    [
        ("production", True),
        ("production", False),
        ("disabled", True),
        ("shadow", False),
    ],
)
def test_contradictory_dual_configuration_is_rejected(
    canonical: str, legacy: bool
) -> None:
    with pytest.raises(ValueError, match="contradicts"):
        production.resolve_mode(
            canonical_present=True,
            canonical_value=canonical,
            legacy_present=True,
            legacy_value=legacy,
        )


def test_invalid_mode_and_non_boolean_legacy_are_rejected() -> None:
    with pytest.raises(ValueError, match="disabled, shadow, or production"):
        production.resolve_mode(
            canonical_present=True,
            canonical_value="canary",
            legacy_present=False,
            legacy_value=None,
        )
    with pytest.raises(ValueError, match="must be boolean"):
        production.resolve_mode(
            canonical_present=False,
            canonical_value=None,
            legacy_present=True,
            legacy_value=1,
        )


def test_legacy_boolean_can_never_enable_production() -> None:
    observed = {
        production.resolve_mode(
            canonical_present=False,
            canonical_value=None,
            legacy_present=True,
            legacy_value=value,
        )[0]
        for value in (False, True)
    }
    assert observed == {"disabled", "shadow"}


@pytest.mark.parametrize("mode", ["disabled", "shadow"])
def test_nonproduction_modes_preserve_exact_baseline_without_policy(mode: str) -> None:
    current_quote = quote()
    baseline = candidate("baseline.jpg", 2.0)
    challenger = candidate("challenger.jpg", 1.0)
    selected, decision = decide(
        [baseline, challenger], baseline, current_quote, mode=mode
    )
    assert selected is baseline
    assert decision["reason"] == f"mode_{mode}"


def test_valid_high_confidence_challenger_is_selected() -> None:
    current_quote = quote()
    baseline = candidate("baseline.jpg", 4.0)
    challenger = candidate("challenger.jpg", 3.5)
    rows = [baseline, challenger]
    policy = validated_policy(rows, current_quote)
    selected, decision = decide(
        rows,
        baseline,
        current_quote,
        policy=policy,
        editorial={"baseline.jpg": {"adjustment": 0}, "challenger.jpg": {"adjustment": 2}},
    )
    assert selected is challenger
    assert decision["action"] == "accept_promotion"
    assert decision["reason"] == "accepted_editorial_promotion"


def test_generated_baseline_is_never_displaced() -> None:
    current_quote = quote()
    baseline = candidate("tg_" + "a" * 64 + ".png", 10.0, source="generated")
    challenger = candidate("challenger.jpg", 9.0)
    rows = [baseline, challenger]
    policy = validated_policy(rows, current_quote)
    selected, decision = decide(rows, baseline, current_quote, policy=policy)
    assert selected is baseline
    assert decision["reason"] == "generated_baseline"


@pytest.mark.parametrize(
    ("classification", "expected"),
    [
        ("baseline_only_historically_specific", "historically_specific_quote"),
        ("baseline_only_unreviewed", "unreviewed_quote"),
    ],
)
def test_quote_classification_guards(
    classification: str, expected: str
) -> None:
    current_quote = quote()
    baseline = candidate("baseline.jpg", 2.0)
    challenger = candidate("challenger.jpg", 1.0)
    rows = [baseline, challenger]
    policy = validated_policy(rows, current_quote, classification=classification)
    selected, decision = decide(rows, baseline, current_quote, policy=policy)
    assert selected is baseline
    assert decision["reason"] == expected


def test_absent_and_changed_quote_hashes_preserve_baseline() -> None:
    current_quote = quote()
    baseline = candidate("baseline.jpg", 2.0)
    challenger = candidate("challenger.jpg", 1.0)
    rows = [baseline, challenger]
    policy = validated_policy(rows, current_quote)
    missing = quote("A newly added quotation")
    selected, decision = decide(rows, baseline, missing, policy=policy)
    assert selected is baseline
    assert decision["reason"] == "unreviewed_quote"
    changed = dict(current_quote, text="Changed bytes")
    selected, decision = decide(rows, baseline, changed, policy=policy)
    assert selected is baseline
    assert decision["reason"] == "quote_hash_mismatch"


def test_blocked_challenger_cannot_be_promoted_but_blocked_baseline_remains() -> None:
    current_quote = quote()
    blocked = candidate("blocked.jpg", 1.0)
    ordinary = candidate("ordinary.jpg", 2.0)
    rows = [ordinary, blocked]
    policy = validated_policy(rows, current_quote, blocked=[blocked["image_hash"]])
    editorial = {"ordinary.jpg": {"adjustment": 0}, "blocked.jpg": {"adjustment": 4}}
    selected, decision = decide(rows, ordinary, current_quote, policy=policy, editorial=editorial)
    assert selected is ordinary
    assert decision["reason"] == "blocked_promotion_image"

    blocked_baseline = candidate(
        "blocked.jpg", 3.0, image_hash=blocked["image_hash"]
    )
    ordinary_challenger = candidate(
        "ordinary.jpg", 2.0, image_hash=ordinary["image_hash"]
    )
    baseline_rows = [ordinary_challenger, blocked_baseline]
    baseline_policy = validated_policy(
        baseline_rows,
        current_quote,
        blocked=[blocked_baseline["image_hash"]],
    )
    selected, decision = decide(
        baseline_rows,
        blocked_baseline,
        current_quote,
        policy=baseline_policy,
        editorial=editorial,
    )
    assert selected is blocked_baseline
    assert decision["reason"] == "baseline_already_best"


@pytest.mark.parametrize(
    ("policy_kwargs", "baseline_score", "challenger_score", "adjustments", "reason"),
    [
        ({"margin": 1.0}, 4.0, 3.5, (0.0, 1.0), "insufficient_policy_margin"),
        ({"loss": 0.25}, 4.0, 3.5, (0.0, 2.0), "excessive_baseline_score_loss"),
        ({"margin": 0.0}, 4.0, 3.0, (0.0, 1.0), "combined_score_tie"),
    ],
)
def test_numeric_guards(
    policy_kwargs: dict,
    baseline_score: float,
    challenger_score: float,
    adjustments: tuple[float, float],
    reason: str,
) -> None:
    current_quote = quote()
    baseline = candidate("baseline.jpg", baseline_score)
    challenger = candidate("challenger.jpg", challenger_score)
    rows = [baseline, challenger]
    policy = validated_policy(rows, current_quote, **policy_kwargs)
    selected, decision = decide(
        rows,
        baseline,
        current_quote,
        policy=policy,
        editorial={
            "baseline.jpg": {"adjustment": adjustments[0]},
            "challenger.jpg": {"adjustment": adjustments[1]},
        },
    )
    assert selected is baseline
    assert decision["reason"] == reason


def test_epsilon_tie_and_equal_best_challengers_preserve_baseline() -> None:
    current_quote = quote()
    baseline = candidate("baseline.jpg", 4.0)
    near = candidate("near.jpg", 3.0)
    rows = [baseline, near]
    policy = validated_policy(rows, current_quote, margin=0.0)
    selected, decision = decide(
        rows,
        baseline,
        current_quote,
        policy=policy,
        editorial={"baseline.jpg": {"adjustment": 0}, "near.jpg": {"adjustment": 1 + 5e-10}},
    )
    assert selected is baseline
    assert decision["reason"] == "combined_score_tie"

    another = candidate("another.jpg", 2.0)
    rows = [baseline, near, another]
    policy = validated_policy(rows, current_quote, margin=0.0)
    selected, decision = decide(
        rows,
        baseline,
        current_quote,
        policy=policy,
        editorial={
            "baseline.jpg": {"adjustment": 0},
            "near.jpg": {"adjustment": 2},
            "another.jpg": {"adjustment": 3},
        },
    )
    assert selected is baseline
    assert decision["reason"] == "ambiguous_best_challenger"


def test_recent_and_near_duplicate_guards() -> None:
    current_quote = quote()
    baseline = candidate("baseline.jpg", 4.0)
    challenger = candidate("challenger.jpg", 3.0)
    rows = [baseline, challenger]
    editorial = {"baseline.jpg": {"adjustment": 0}, "challenger.jpg": {"adjustment": 2}}
    policy = validated_policy(rows, current_quote, gap=2)
    recent = [{"post_id": "123", "image_basename": "old-name.jpg", "image_sha256": challenger["image_hash"]}]
    selected, decision = decide(rows, baseline, current_quote, policy=policy, editorial=editorial, recent=recent)
    assert selected is baseline
    assert decision["reason"] == "recent_confirmed_image"

    policy = validated_policy(
        rows,
        current_quote,
        gap=0,
        clusters=[[baseline["image_hash"], challenger["image_hash"]]],
    )
    selected, decision = decide(rows, baseline, current_quote, policy=policy, editorial=editorial)
    assert selected is baseline
    assert decision["reason"] == "near_duplicate"


def test_no_editorial_challenger_and_closed_breaker_fallbacks() -> None:
    current_quote = quote()
    baseline = candidate("baseline.jpg", 2.0)
    challenger = candidate("challenger.jpg", 1.0)
    rows = [baseline, challenger]
    policy = validated_policy(rows, current_quote)
    selected, decision = decide(
        rows,
        baseline,
        current_quote,
        policy=policy,
        editorial={"baseline.jpg": {"adjustment": 0}},
    )
    assert selected is baseline
    assert decision["reason"] == "no_valid_editorial_challenger"
    assert decide(rows, baseline, current_quote)[1]["reason"] == "policy_unavailable"
    assert decide(rows, baseline, current_quote, policy=policy, breaker=True)[1]["reason"] == "circuit_breaker_open"


def test_runtime_parameter_and_candidate_integrity_failures() -> None:
    current_quote = quote()
    baseline = candidate("baseline.jpg", 2.0)
    challenger = candidate("challenger.jpg", 1.0)
    rows = [baseline, challenger]
    policy = validated_policy(rows, current_quote)
    with pytest.raises(production.EditorialDecisionIntegrityError, match="parameters") as error:
        decide(rows, baseline, current_quote, policy=policy, weight=0.31)
    assert error.value.reason == "runtime_parameter_mismatch"
    for invalid in (math.nan, math.inf, -math.inf):
        bad = candidate("bad.jpg", invalid)
        bad_rows = [baseline, bad]
        bad_policy = validated_policy(bad_rows, current_quote)
        with pytest.raises(production.EditorialDecisionIntegrityError) as error:
            decide(bad_rows, baseline, current_quote, policy=bad_policy)
        assert error.value.reason == "non_finite_score"
    with pytest.raises(production.EditorialDecisionIntegrityError) as error:
        decide([challenger], baseline, current_quote, policy=policy)
    assert error.value.reason == "baseline_not_in_candidates"
    changed = copy.deepcopy(challenger)
    changed["image_hash"] = digest("changed")
    with pytest.raises(production.EditorialDecisionIntegrityError) as error:
        decide([baseline, changed], baseline, current_quote, policy=policy, editorial={"baseline.jpg": {"adjustment": 0}, "challenger.jpg": {"adjustment": 4}})
    assert error.value.reason == "candidate_content_hash_mismatch"

    colliding = copy.deepcopy(challenger)
    colliding["image_hash"] = baseline["image_hash"]
    with pytest.raises(production.EditorialDecisionIntegrityError) as error:
        decide([baseline, colliding], baseline, current_quote, policy=policy)
    assert error.value.reason == "candidate_identity_collision"

    impossible_baseline = candidate("lower.jpg", 1.0)
    higher = candidate("higher.jpg", 2.0)
    impossible_rows = [impossible_baseline, higher]
    impossible_policy = validated_policy(impossible_rows, current_quote)
    with pytest.raises(production.EditorialDecisionIntegrityError) as error:
        decide(
            impossible_rows,
            impossible_baseline,
            current_quote,
            policy=impossible_policy,
        )
    assert error.value.reason == "impossible_score_ordering"


def test_process_breaker_latches_first_failure_and_counts_subsequent() -> None:
    breaker = production.EditorialCircuitBreaker()
    breaker.reset_for_process(resolved_mode="production", mode_source="canonical")
    assert breaker.open("non_finite_score") is True
    first = breaker.snapshot()
    assert breaker.open("runtime_parameter_mismatch") is False
    second = breaker.snapshot()
    assert second["open"] is True
    assert second["first_failure_reason"] == "non_finite_score"
    assert second["first_failure_time"] == first["first_failure_time"]
    assert second["failure_count"] == 2


def test_policy_loader_is_strict_canonical_and_stale_safe(tmp_path: Path) -> None:
    current_quote = quote()
    rows = [candidate("a.jpg", 2.0), candidate("b.jpg", 1.0)]
    document, inputs, originals = policy_document(rows, current_quote)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps(document, indent=2), encoding="utf-8")
    second.write_text(json.dumps(document, separators=(",", ":"), sort_keys=False), encoding="utf-8")
    loaded_first = production.load_validated_policy(
        first,
        runtime_weight=0.32,
        runtime_maximum_abs_adjustment=4.0,
        runtime_input_sha256=inputs,
        current_original_sha256_by_basename=originals,
        current_quote_hashes=[current_quote["quote_hash"]],
    )
    loaded_second = production.load_validated_policy(
        second,
        runtime_weight=0.32,
        runtime_maximum_abs_adjustment=4.0,
        runtime_input_sha256=inputs,
        current_original_sha256_by_basename=originals,
        current_quote_hashes=[current_quote["quote_hash"]],
    )
    assert loaded_first.policy_sha256 == loaded_second.policy_sha256

    for stale_key in sorted(production.RUNTIME_INPUT_SHA256_KEYS):
        stale = dict(inputs)
        stale[stale_key] = digest(f"changed {stale_key}")
        with pytest.raises(production.EditorialPolicyError) as error:
            production.load_validated_policy(
                first,
                runtime_weight=0.32,
                runtime_maximum_abs_adjustment=4.0,
                runtime_input_sha256=stale,
                current_original_sha256_by_basename=originals,
                current_quote_hashes=[current_quote["quote_hash"]],
            )
        assert error.value.reason == "policy_stale"
        assert error.value.policy_id == document["policy_id"]
        assert error.value.policy_sha256 == production.canonical_sha256(document)

    missing_classification = copy.deepcopy(document)
    missing_classification["quote_policy"] = {}
    with pytest.raises(production.EditorialPolicyError) as error:
        production.validate_policy_document(
            missing_classification,
            runtime_weight=0.32,
            runtime_maximum_abs_adjustment=4.0,
            runtime_input_sha256=inputs,
            current_original_sha256_by_basename=originals,
            current_quote_hashes=[current_quote["quote_hash"]],
        )
    assert error.value.reason == "policy_invalid"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("editorial_weight", True),
        ("maximum_abs_adjustment", math.inf),
        ("minimum_policy_margin", math.nan),
        ("maximum_baseline_score_loss", False),
        ("minimum_confirmed_post_gap", True),
    ],
)
def test_policy_rejects_boolean_and_nonfinite_numbers(field: str, value: object) -> None:
    current_quote = quote()
    rows = [candidate("a.jpg", 2.0), candidate("b.jpg", 1.0)]
    document, inputs, originals = policy_document(rows, current_quote)
    document[field] = value
    with pytest.raises(production.EditorialPolicyError) as error:
        production.validate_policy_document(
            document,
            runtime_weight=0.32,
            runtime_maximum_abs_adjustment=4.0,
            runtime_input_sha256=inputs,
            current_original_sha256_by_basename=originals,
            current_quote_hashes=[current_quote["quote_hash"]],
        )
    assert error.value.reason == "policy_invalid"


def test_policy_rejects_malformed_duplicate_or_noncurrent_image_hashes() -> None:
    current_quote = quote()
    rows = [
        candidate("a.jpg", 3.0),
        candidate("b.jpg", 2.0),
        candidate("c.jpg", 1.0),
    ]
    document, inputs, originals = policy_document(rows, current_quote)
    hashes = [row["image_hash"] for row in rows]
    invalid_documents: list[dict] = []
    for blocked in (
        [hashes[0], hashes[0]],
        ["not-a-sha256"],
        [digest("absent original")],
    ):
        changed = copy.deepcopy(document)
        changed["blocked_promotion_image_sha256"] = blocked
        invalid_documents.append(changed)
    for clusters in (
        [[hashes[0]]],
        [[hashes[0], hashes[0]]],
        [[hashes[0], "not-a-sha256"]],
        [[hashes[0], digest("absent original")]],
        [[hashes[0], hashes[1]], [hashes[1], hashes[2]]],
    ):
        changed = copy.deepcopy(document)
        changed["near_duplicate_clusters"] = clusters
        invalid_documents.append(changed)

    for changed in invalid_documents:
        with pytest.raises(production.EditorialPolicyError) as error:
            production.validate_policy_document(
                changed,
                runtime_weight=0.32,
                runtime_maximum_abs_adjustment=4.0,
                runtime_input_sha256=inputs,
                current_original_sha256_by_basename=originals,
                current_quote_hashes=[current_quote["quote_hash"]],
            )
        assert error.value.reason == "policy_invalid"

    colliding_originals = dict(originals)
    colliding_originals["c.jpg"] = colliding_originals["b.jpg"]
    with pytest.raises(production.EditorialPolicyError) as error:
        production.validate_policy_document(
            document,
            runtime_weight=0.32,
            runtime_maximum_abs_adjustment=4.0,
            runtime_input_sha256=inputs,
            current_original_sha256_by_basename=colliding_originals,
            current_quote_hashes=[current_quote["quote_hash"]],
        )
    assert error.value.reason == "policy_invalid"


def test_unauthorised_policy_cannot_be_loaded(tmp_path: Path) -> None:
    current_quote = quote()
    rows = [candidate("a.jpg", 2.0), candidate("b.jpg", 1.0)]
    document, inputs, originals = policy_document(rows, current_quote, authorised=False)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(production.EditorialPolicyError) as error:
        production.load_validated_policy(
            path,
            runtime_weight=0.32,
            runtime_maximum_abs_adjustment=4.0,
            runtime_input_sha256=inputs,
            current_original_sha256_by_basename=originals,
            current_quote_hashes=[current_quote["quote_hash"]],
        )
    assert error.value.reason == "policy_unauthorised"
    assert error.value.policy_id == document["policy_id"]
    assert error.value.policy_sha256 == production.canonical_sha256(document)


def test_seeded_invariants_over_thousands_of_candidate_sets() -> None:
    rng = random.Random(20260903)
    current_quote = quote()
    for iteration in range(2500):
        count = 2 + iteration % 7
        rows = [
            candidate(f"image-{iteration}-{index}.jpg", rng.uniform(-8, 8))
            for index in range(count)
        ]
        baseline = max(rows, key=lambda row: row["score"])
        policy = validated_policy(rows, current_quote, margin=0.25, loss=3.0, gap=0)
        editorial = {
            row["basename"]: {"adjustment": rng.uniform(-4, 4)} for row in rows
        }
        before_rows = copy.deepcopy(rows)
        before_quote = copy.deepcopy(current_quote)
        before_editorial = copy.deepcopy(editorial)
        global_rng = random.getstate()
        selected, first = decide(
            rows,
            baseline,
            current_quote,
            policy=policy,
            editorial=editorial,
        )
        selected_again, second = decide(
            rows,
            baseline,
            current_quote,
            policy=policy,
            editorial=editorial,
        )
        assert random.getstate() == global_rng
        assert rows == before_rows
        assert current_quote == before_quote
        assert editorial == before_editorial
        assert any(selected is row for row in rows)
        assert selected_again is selected
        assert first == second
        assert first["action"] in production.DECISION_ACTIONS
        assert first["reason"] in production.DECISION_REASONS
        for side in (first["baseline"], first["challenger"]):
            for field in ("raw_score", "editorial_adjustment", "combined_score"):
                assert side[field] is None or math.isfinite(side[field])


def test_dictionary_insertion_order_does_not_change_nontied_result() -> None:
    current_quote = quote()
    baseline = candidate("baseline.jpg", 5.0)
    challenger = candidate("challenger.jpg", 4.0)
    rows = [baseline, challenger]
    policy = validated_policy(rows, current_quote)
    editorial = {"baseline.jpg": {"adjustment": 0}, "challenger.jpg": {"adjustment": 3}}
    first_selected, first = decide(rows, baseline, current_quote, policy=policy, editorial=editorial)
    reordered_rows = [dict(reversed(list(row.items()))) for row in rows]
    reordered_baseline = reordered_rows[0]
    reordered_editorial = dict(reversed(list(editorial.items())))
    second_selected, second = decide(reordered_rows, reordered_baseline, dict(reversed(list(current_quote.items()))), policy=policy, editorial=reordered_editorial)
    assert first_selected["basename"] == second_selected["basename"]
    assert first == second

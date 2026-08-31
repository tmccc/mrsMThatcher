from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from tools import proposition_ledger_author_groups as author_groups


PROJECT_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_DIR / "tools/proposition_ledger_author_groups.py"
PRIVATE_TEST_HMAC_KEY = bytes(range(32))


def _conversation(
    conversation_key: str,
    *,
    scheme: str | None = "prospective-v4-hmac-domain",
    principal: str | None = "opaque-author-one",
    exposure: str = "genuinely_unexposed",
    categories: tuple[str, ...] = ("unexposed_candidate",),
    reasons: tuple[str, ...] = (),
    cross_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "conversation_key": conversation_key,
        "author_key_scheme": scheme,
        "principal_author_key": principal,
        "prior_exposure_status": exposure,
        "prior_exposure_categories": list(categories),
        "prior_exposure_reasons": list(reasons),
    }
    row.update(
        cross_identity
        or {
            "cross_family_author_group_key": None,
            "cross_family_author_identity_status": "unavailable",
            "cross_family_author_identity_reasons": [
                "authoritative_raw_identity_unavailable_in_both_source_families"
            ],
        }
    )
    return row


def _target(
    conversation_key: str,
    target_key: str,
    *,
    scheme: str = "prospective-v4-hmac-domain",
    principal: str = "opaque-author-one",
    exposure: str = "genuinely_unexposed",
    identity_status: str | None = None,
    identity_reasons: tuple[str, ...] = (),
) -> dict[str, Any]:
    row = {
        "target_key": target_key,
        "conversation_key": conversation_key,
        "author_key_scheme": scheme,
        "principal_author_key": principal,
        "reconstruction_grade": "A",
        "stability_status": "quiescent_at_frozen_cutoff",
        "target_sequence_class": "persistent_multiturn_target",
        "conversation_exposure_status": exposure,
        "target_exposure_status": exposure,
        "effective_exposure_status": exposure,
        "complete_target_ancestry": True,
        "outcome_evidence_class": "confirmed_published_reply",
    }
    if identity_status is not None:
        row["target_author_identity_status"] = identity_status
        row["target_author_identity_reasons"] = list(identity_reasons)
    return row


def _observation(
    conversation_key: str,
    *,
    scheme: str | None = "prospective-v4-hmac-domain",
    principal: str | None = "opaque-author-one",
    exposure: str = "genuinely_unexposed",
    categories: tuple[str, ...] = ("unexposed_candidate",),
    reasons: tuple[str, ...] = (),
    binding_status: str = "available",
    cross_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = _conversation(
        conversation_key,
        scheme=scheme,
        principal=principal,
        exposure=exposure,
        categories=categories,
        reasons=reasons,
        cross_identity=cross_identity,
    )
    row["contributor_identity_binding_status"] = binding_status
    row["contributor_identity_binding_reasons"] = (
        ["synthetic_scoped_binding_unresolved"]
        if binding_status == "unresolved"
        else []
    )
    return row


def _one_target(result: dict[str, Any]) -> dict[str, Any]:
    assert len(result["target_rows"]) == 1
    return result["target_rows"][0]


def test_exposed_conversation_in_same_group_disqualifies_clean_target() -> None:
    conversations = [
        _conversation("clean"),
        _conversation(
            "exposed",
            exposure="exposed",
            categories=("development_labelled",),
            reasons=("development_label_registry",),
        ),
    ]
    result = author_groups.apply_author_group_exposure(
        [_target("clean", "target-clean")], conversations
    )
    target = _one_target(result)

    assert target["author_group_exposure_status"] == "contains_direct_exposure"
    assert target["author_group_conversation_count"] == 2
    assert target["author_group_target_count"] == 1
    assert target["author_group_contains_directly_exposed_material"] is True
    assert "development_labelled" in target["author_group_exposure_categories"]
    assert "development_label_registry" in target["author_group_exposure_reasons"]
    assert target["preliminary_within_family_held_out_eligibility"] is False
    assert (
        "direct_exposure_elsewhere_in_within_family_author_group"
        in target["preliminary_within_family_held_out_exclusion_reasons"]
    )


@pytest.mark.parametrize("direct_category", ["calibration", "prior_model_experiment"])
def test_calibration_and_prior_model_experiment_disqualify_same_group(
    direct_category: str,
) -> None:
    conversations = [
        _conversation("candidate"),
        _conversation(
            "direct",
            exposure="exposed",
            categories=(direct_category,),
            reasons=(f"registry:{direct_category}",),
        ),
    ]
    target = _one_target(
        author_groups.apply_author_group_exposure(
            [_target("candidate", "candidate-target")], conversations
        )
    )

    assert direct_category in target["author_group_exposure_categories"]
    assert target["preliminary_within_family_held_out_eligibility"] is False


def test_same_pseudonym_in_different_identity_domains_remains_separate() -> None:
    conversations = [
        _conversation(
            "domain-a-exposed",
            scheme="domain-a",
            principal="same-pseudonym",
            exposure="exposed",
            categories=("prior_human_review",),
        ),
        _conversation(
            "domain-b-clean",
            scheme="domain-b",
            principal="same-pseudonym",
        ),
    ]
    targets = [
        _target(
            "domain-a-exposed",
            "target-a",
            scheme="domain-a",
            principal="same-pseudonym",
            exposure="exposed",
        ),
        _target(
            "domain-b-clean",
            "target-b",
            scheme="domain-b",
            principal="same-pseudonym",
        ),
    ]
    result = author_groups.apply_author_group_exposure(targets, conversations)
    by_key = {row["target_key"]: row for row in result["target_rows"]}

    assert (
        by_key["target-a"]["within_family_author_group_key"]
        != by_key["target-b"]["within_family_author_group_key"]
    )
    assert by_key["target-b"]["author_group_conversation_count"] == 1
    assert by_key["target-b"]["preliminary_within_family_held_out_eligibility"]


def test_structurally_mined_group_is_reported_and_requires_groupwise_split() -> None:
    conversations = [
        _conversation("clean"),
        _conversation(
            "structural",
            exposure="structurally_mined_only",
            categories=("structurally_mined_only",),
            reasons=("frozen_structural_extractor_input",),
        ),
    ]
    result = author_groups.apply_author_group_exposure(
        [_target("clean", "clean-target")], conversations
    )
    target = _one_target(result)

    assert (
        target["author_group_exposure_status"]
        == "mixed_unexposed_and_structurally_mined"
    )
    assert target["author_group_contains_structurally_mined_material"] is True
    assert target["author_group_contains_genuinely_unexposed_material"] is True
    assert target["author_group_requires_groupwise_split"] is True
    assert target["author_group_split_assignment"] is None
    # Structural mining is a split constraint, not direct exposure.
    assert target["preliminary_within_family_held_out_eligibility"] is True


def test_completely_unexposed_group_remains_within_family_eligible() -> None:
    result = author_groups.apply_author_group_exposure(
        [_target("clean", "clean-target")], [_conversation("clean")]
    )
    target = _one_target(result)

    assert target["author_group_exposure_status"] == "clean_genuinely_unexposed_group"
    assert target["author_group_contains_directly_exposed_material"] is False
    assert target["preliminary_within_family_held_out_eligibility"] is True
    assert target["preliminary_within_family_held_out_exclusion_reasons"] == []


def test_multi_author_conversation_targets_use_their_own_complete_tuples() -> None:
    conversations = [
        _conversation("shared", principal="synthetic-contributor-a")
    ]
    observations = [
        _observation("shared", principal="synthetic-contributor-a"),
        _observation("shared", principal="synthetic-contributor-b"),
    ]
    targets = [
        _target(
            "shared",
            "target-a",
            principal="synthetic-contributor-a",
            identity_status="available",
        ),
        _target(
            "shared",
            "target-b",
            principal="synthetic-contributor-b",
            identity_status="available",
        ),
    ]

    result = author_groups.apply_author_group_exposure(
        targets, conversations, observations
    )
    by_key = {row["target_key"]: row for row in result["target_rows"]}

    assert by_key["target-b"]["principal_author_key"] == "synthetic-contributor-b"
    assert by_key["target-a"]["within_family_author_group_key"] != by_key[
        "target-b"
    ]["within_family_author_group_key"]
    assert all(
        row["author_group_exposure_status"] != "identity_group_conflicting"
        for row in by_key.values()
    )
    assert len(result["conversation_rows"]) == 1
    assert "within_family_author_group_key" not in result["conversation_rows"][0]
    assert "author_group_exposure_status" not in result["conversation_rows"][0]
    assert len(result["within_family_author_groups"]) == 2


def test_missing_exact_target_author_never_inherits_conversation_principal() -> None:
    target = _target(
        "shared",
        "missing-author",
        principal="placeholder-removed",
        identity_status="unavailable",
        identity_reasons=("target_author_identity_unavailable",),
    )
    target.pop("author_key_scheme")
    target.pop("principal_author_key")

    annotated = _one_target(
        author_groups.apply_author_group_exposure(
            [target],
            [_conversation("shared", principal="synthetic-contributor-a")],
            [_observation("shared", principal="synthetic-contributor-a")],
        )
    )

    assert annotated["target_author_identity_status"] == "unavailable"
    assert annotated["within_family_author_group_key"] is None
    assert annotated["preliminary_within_family_held_out_eligibility"] is False
    assert (
        "target_author_identity_unavailable"
        in annotated["preliminary_within_family_held_out_exclusion_reasons"]
    )


def test_exposed_multi_author_conversation_contaminates_every_contributor() -> None:
    conversations = [
        _conversation("exposed-shared", principal="synthetic-contributor-a"),
        _conversation("clean-b", principal="synthetic-contributor-b"),
    ]
    observations = [
        _observation(
            "exposed-shared",
            principal="synthetic-contributor-a",
            exposure="exposed",
            categories=("prior_human_review",),
        ),
        _observation(
            "exposed-shared",
            principal="synthetic-contributor-b",
            exposure="exposed",
            categories=("prior_human_review",),
        ),
        _observation("clean-b", principal="synthetic-contributor-b"),
    ]
    target = _target(
        "clean-b",
        "clean-target-b",
        principal="synthetic-contributor-b",
        identity_status="available",
    )

    annotated = _one_target(
        author_groups.apply_author_group_exposure(
            [target], conversations, observations
        )
    )

    assert annotated["author_group_conversation_count"] == 2
    assert annotated["author_group_contains_directly_exposed_material"] is True
    assert annotated["preliminary_within_family_held_out_eligibility"] is False


def test_scoped_exposure_for_a_does_not_contaminate_b_in_shared_conversation() -> None:
    conversations = [
        _conversation(
            "shared",
            principal="synthetic-contributor-a",
            exposure="exposed",
            categories=("prior_model_experiment",),
        )
    ]
    observations = [
        _observation(
            "shared",
            principal="synthetic-contributor-a",
            exposure="exposed",
            categories=("prior_model_experiment",),
        ),
        _observation("shared", principal="synthetic-contributor-b"),
    ]
    targets = [
        _target(
            "shared",
            "target-a",
            principal="synthetic-contributor-a",
            exposure="exposed",
            identity_status="available",
        ),
        _target(
            "shared",
            "target-b",
            principal="synthetic-contributor-b",
            identity_status="available",
        ),
    ]
    targets[1]["conversation_exposure_status"] = "exposed"
    targets[1]["effective_exposure_status"] = "exposed"
    targets[1]["author_group_conversation_exposure_status"] = (
        "genuinely_unexposed"
    )
    targets[1]["author_group_target_exposure_status"] = "genuinely_unexposed"
    targets[1]["author_group_effective_exposure_status"] = "genuinely_unexposed"

    result = author_groups.apply_author_group_exposure(
        targets, conversations, observations
    )
    by_key = {row["target_key"]: row for row in result["target_rows"]}

    assert by_key["target-a"]["preliminary_within_family_held_out_eligibility"] is False
    assert by_key["target-b"]["effective_exposure_status"] == "exposed"
    assert by_key["target-b"]["author_group_contains_directly_exposed_material"] is False
    assert by_key["target-b"]["preliminary_within_family_held_out_eligibility"] is True


def test_structural_only_observation_is_a_split_constraint_not_direct_exposure() -> None:
    observations = [
        _observation(
            "structural-b",
            principal="synthetic-contributor-b",
            exposure="structurally_mined_only",
            categories=("structurally_mined_only",),
        ),
        _observation("clean-b", principal="synthetic-contributor-b"),
    ]
    target = _target(
        "clean-b",
        "clean-target-b",
        principal="synthetic-contributor-b",
        identity_status="available",
    )

    annotated = _one_target(
        author_groups.apply_author_group_exposure(
            [target],
            [_conversation("structural-b"), _conversation("clean-b")],
            observations,
        )
    )

    assert annotated["author_group_contains_structurally_mined_material"] is True
    assert annotated["author_group_contains_directly_exposed_material"] is False
    assert annotated["author_group_requires_groupwise_split"] is True
    assert annotated["preliminary_within_family_held_out_eligibility"] is True


def test_unresolved_scoped_observation_fails_closed_without_first_author_choice() -> None:
    observations = [
        _observation("shared", principal="synthetic-contributor-a"),
        _observation("shared", principal="synthetic-contributor-b"),
        _observation(
            "shared",
            scheme=None,
            principal=None,
            exposure="exposed",
            categories=("prior_model_experiment",),
            binding_status="unresolved",
        ),
    ]
    targets = [
        _target(
            "shared",
            "target-a",
            principal="synthetic-contributor-a",
            identity_status="available",
        ),
        _target(
            "shared",
            "target-b",
            principal="synthetic-contributor-b",
            identity_status="available",
        ),
    ]

    result = author_groups.apply_author_group_exposure(
        targets,
        [_conversation("shared", principal="synthetic-contributor-a")],
        observations,
    )

    assert all(
        row["preliminary_within_family_held_out_eligibility"] is False
        and "scoped_exposure_contributor_unresolved"
        in row["preliminary_within_family_held_out_exclusion_reasons"]
        for row in result["target_rows"]
    )


def test_conflicting_scoped_observation_remains_conflicting_and_fails_closed() -> None:
    observations = [
        _observation(
            "shared-conflict",
            principal="synthetic-contributor-a",
            exposure="exposed",
            categories=("prior_model_experiment",),
            binding_status="conflicting",
        ),
        _observation(
            "shared-conflict",
            principal="synthetic-contributor-b",
        ),
    ]
    targets = [
        _target(
            "shared-conflict",
            "target-a",
            principal="synthetic-contributor-a",
            identity_status="available",
        ),
        _target(
            "shared-conflict",
            "target-b",
            principal="synthetic-contributor-b",
            identity_status="available",
        ),
    ]

    result = author_groups.apply_author_group_exposure(
        targets,
        [_conversation("shared-conflict", principal="synthetic-contributor-a")],
        observations,
    )

    assert all(
        row["preliminary_within_family_held_out_eligibility"] is False
        and "scoped_exposure_contributor_unresolved"
        in row["preliminary_within_family_held_out_exclusion_reasons"]
        for row in result["target_rows"]
    )
    conflicting = next(
        row
        for row in result["contributor_exposure_observations"]
        if row["principal_author_key"] == "synthetic-contributor-a"
    )
    assert conflicting["contributor_identity_binding_status"] == "conflicting"
    assert conflicting["within_family_author_identity_status"] == "conflicting"


def test_contradictory_cross_family_keys_for_one_within_identity_conflict() -> None:
    observation = _observation(
        "cross-conflict",
        cross_identity={
            "cross_family_author_group_key": "synthetic-cross-group-one",
            "cross_family_author_identity_status": "available",
            "cross_family_author_identity_reasons": [],
        },
    )
    target = _target("cross-conflict", "cross-conflict-target")
    target.update(
        {
            "cross_family_author_group_key": "synthetic-cross-group-two",
            "cross_family_author_identity_status": "available",
            "cross_family_author_identity_reasons": [],
        }
    )

    result = author_groups.apply_author_group_exposure(
        [target], [_conversation("cross-conflict")], [observation]
    )

    annotated_target = _one_target(result)
    annotated_observation = result["contributor_exposure_observations"][0]
    assert annotated_target["cross_family_author_identity_status"] == "conflicting"
    assert annotated_target["cross_family_author_group_key"] is None
    assert (
        annotated_target["preliminary_cross_family_clean_held_out_eligibility"]
        is False
    )
    assert (
        "cross_family_author_identity_conflicting"
        in annotated_target[
            "preliminary_cross_family_clean_held_out_exclusion_reasons"
        ]
    )
    assert (
        annotated_observation["cross_family_author_identity_status"]
        == "conflicting"
    )
    assert result["cross_family_author_groups"] == []


@pytest.mark.parametrize("observation_status", ["unavailable", "conflicting"])
def test_mixed_cross_family_statuses_in_one_conversation_conflict(
    observation_status: str,
) -> None:
    observation = _observation(
        "cross-status-conflict",
        cross_identity={
            "cross_family_author_group_key": None,
            "cross_family_author_identity_status": observation_status,
            "cross_family_author_identity_reasons": [
                "synthetic_cross_identity_not_available"
            ],
        },
    )
    target = _target("cross-status-conflict", "cross-status-target")
    target.update(
        {
            "cross_family_author_group_key": "synthetic-cross-group",
            "cross_family_author_identity_status": "available",
            "cross_family_author_identity_reasons": [],
        }
    )

    result = author_groups.apply_author_group_exposure(
        [target], [_conversation("cross-status-conflict")], [observation]
    )

    assert result["target_rows"][0]["cross_family_author_identity_status"] == (
        "conflicting"
    )
    assert result["contributor_exposure_observations"][0][
        "cross_family_author_identity_status"
    ] == "conflicting"
    assert result["cross_family_author_groups"] == []


def test_cross_source_hmac_key_is_deterministic_and_family_independent() -> None:
    first = author_groups.derive_cross_family_author_identity(
        PRIVATE_TEST_HMAC_KEY,
        "authoritative-private-identity-001",
        authoritative_identity_available_in_both_families=True,
    )
    second = author_groups.derive_cross_family_author_identity(
        PRIVATE_TEST_HMAC_KEY,
        "authoritative-private-identity-001",
        authoritative_identity_available_in_both_families=True,
    )

    assert first == second
    assert first["cross_family_author_identity_status"] == "available"
    assert first["cross_family_author_group_key"].startswith(
        "cross-family-author-group-hmac-sha256-v1-"
    )
    assert author_groups.CROSS_SOURCE_HMAC_PURPOSE.endswith(
        "cross-source-author-group/v1"
    )
    parameters = inspect.signature(
        author_groups.derive_cross_family_author_identity
    ).parameters
    assert "source_family" not in parameters


def test_raw_identity_is_consumed_but_never_returned_or_accepted_in_rows() -> None:
    raw_identity = "never-emit-this-authoritative-id"
    descriptor = author_groups.derive_cross_family_author_identity(
        PRIVATE_TEST_HMAC_KEY,
        raw_identity,
        authoritative_identity_available_in_both_families=True,
    )
    assert raw_identity not in json.dumps(descriptor, sort_keys=True)

    forbidden = _conversation("unsafe")
    forbidden["raw_contributor_id"] = raw_identity
    with pytest.raises(author_groups.AuthorGroupError, match="forbidden raw identity"):
        author_groups.apply_author_group_exposure([], [forbidden])


def test_no_cross_family_key_is_fabricated_when_common_identity_is_unavailable() -> None:
    descriptor = author_groups.derive_cross_family_author_identity(
        PRIVATE_TEST_HMAC_KEY,
        "identity-present-in-one-family-only",
        authoritative_identity_available_in_both_families=False,
    )

    assert descriptor["cross_family_author_group_key"] is None
    assert descriptor["cross_family_author_identity_status"] == "unavailable"
    assert descriptor["cross_family_author_identity_reasons"] == [
        "authoritative_raw_identity_unavailable_in_both_source_families"
    ]


def test_cross_family_clean_eligibility_is_pending_when_identity_unavailable() -> None:
    target = _one_target(
        author_groups.apply_author_group_exposure(
            [_target("clean", "clean-target")], [_conversation("clean")]
        )
    )

    assert target["preliminary_within_family_held_out_eligibility"] is True
    assert target["cross_family_author_group_key"] is None
    assert target["cross_family_author_identity_status"] == "unavailable"
    assert target["preliminary_cross_family_clean_held_out_eligibility"] is False
    assert (
        target["preliminary_cross_family_clean_held_out_eligibility_status"]
        == "pending_cross_family_identity_unavailable"
    )
    assert target["preliminary_cross_family_clean_held_out_exclusion_reasons"] == [
        "cross_family_author_identity_unavailable"
    ]


def test_common_cross_family_group_enforces_direct_exposure() -> None:
    descriptor = author_groups.derive_cross_family_author_identity(
        PRIVATE_TEST_HMAC_KEY,
        "shared-private-id",
        authoritative_identity_available_in_both_families=True,
    )
    conversations = [
        _conversation(
            "benchmark-clean",
            scheme="benchmark-domain",
            principal="benchmark-pseudonym",
            cross_identity=descriptor,
        ),
        _conversation(
            "prospective-exposed",
            scheme="prospective-domain",
            principal="prospective-pseudonym",
            exposure="exposed",
            categories=("report_excerpt",),
            cross_identity=descriptor,
        ),
    ]
    target = _one_target(
        author_groups.apply_author_group_exposure(
            [
                _target(
                    "benchmark-clean",
                    "clean-target",
                    scheme="benchmark-domain",
                    principal="benchmark-pseudonym",
                )
            ],
            conversations,
        )
    )

    assert target["preliminary_within_family_held_out_eligibility"] is True
    assert target["cross_family_author_identity_status"] == "available"
    assert target["cross_family_author_group_contains_directly_exposed_material"]
    assert target["preliminary_cross_family_clean_held_out_eligibility"] is False
    assert (
        "direct_exposure_elsewhere_in_cross_family_author_group"
        in target["preliminary_cross_family_clean_held_out_exclusion_reasons"]
    )


def test_crosstab_totals_exactly_reproduce_rows_and_group_classifications() -> None:
    conversations = [
        _conversation("group-one-a", principal="group-one"),
        _conversation(
            "group-one-b",
            principal="group-one",
            exposure="structurally_mined_only",
            categories=("structurally_mined_only",),
        ),
        _conversation(
            "group-two",
            principal="group-two",
            exposure="exposed",
            categories=("calibration",),
        ),
        _conversation("unavailable", scheme=None, principal=None),
    ]
    targets = [
        _target("group-one-a", "target-one", principal="group-one"),
        _target(
            "group-two", "target-two", principal="group-two", exposure="exposed"
        ),
        _target(
            "unavailable",
            "target-three",
            scheme="temporary-missing-domain",
            principal="temporary-missing-key",
        ),
    ]
    # Keep the third conversation consistently unavailable across observations.
    targets[-1].pop("author_key_scheme")
    targets[-1].pop("principal_author_key")
    result = author_groups.apply_author_group_exposure(targets, conversations)
    crosstab = result["crosstab"]

    assert crosstab["target_count"] == len(result["target_rows"])
    assert crosstab["conversation_count"] == len(result["conversation_rows"])
    assert crosstab["contributor_exposure_observation_count"] == len(
        result["contributor_exposure_observations"]
    )
    assert sum(
        crosstab["dimensions"]["target_author_group_exposure_status"].values()
    ) == len(result["target_rows"])
    assert sum(
        crosstab["dimensions"]["within_family_group_exposure_status"].values()
    ) == len(result["within_family_author_groups"])
    assert (
        author_groups.author_group_crosstab_errors(
            result["target_rows"],
            result["conversation_rows"],
            result["within_family_author_groups"],
            result["cross_family_author_groups"],
            crosstab,
            result["contributor_exposure_observations"],
        )
        == []
    )


def test_reordering_targets_and_contributor_observations_is_invariant() -> None:
    conversations = [
        _conversation("shared", principal="synthetic-contributor-a"),
        _conversation("other", principal="synthetic-contributor-b"),
    ]
    observations = [
        _observation("shared", principal="synthetic-contributor-a"),
        _observation("shared", principal="synthetic-contributor-b"),
        _observation(
            "other",
            principal="synthetic-contributor-b",
            exposure="structurally_mined_only",
            categories=("structurally_mined_only",),
        ),
    ]
    targets = [
        _target(
            "shared",
            "target-a",
            principal="synthetic-contributor-a",
            identity_status="available",
        ),
        _target(
            "shared",
            "target-b",
            principal="synthetic-contributor-b",
            identity_status="available",
        ),
    ]

    forward = author_groups.apply_author_group_exposure(
        targets, conversations, observations
    )
    reverse = author_groups.apply_author_group_exposure(
        list(reversed(targets)),
        list(reversed(conversations)),
        list(reversed(observations)),
    )

    assert forward == reverse


def test_contributor_observation_privacy_and_deterministic_keys() -> None:
    unsafe = _observation("unsafe", principal="synthetic-contributor")
    unsafe["raw_contributor_id"] = "synthetic-raw-identity-never-emit"
    with pytest.raises(author_groups.AuthorGroupError, match="forbidden raw identity"):
        author_groups.apply_author_group_exposure([], [], [unsafe])

    result = author_groups.apply_author_group_exposure(
        [],
        [_conversation("safe", principal="synthetic-contributor")],
        [_observation("safe", principal="synthetic-contributor")],
    )
    observation = result["contributor_exposure_observations"][0]

    assert result["schema_version"] == "proposition-ledger-author-group-exposure-v2"
    assert observation["contributor_exposure_observation_key"].startswith(
        "contributor-exposure-observation-sha256-v1-"
    )
    assert "synthetic-raw-identity-never-emit" not in json.dumps(
        result, sort_keys=True
    )


def test_identity_conflict_is_explicit_and_not_grouped() -> None:
    conversation = _conversation("conflict", scheme="domain-a", principal="key-a")
    target = _target(
        "conflict",
        "target",
        scheme="domain-b",
        principal="exact-target-key",
        identity_status="conflicting",
        identity_reasons=("target_author_identity_conflicting",),
    )
    annotated = _one_target(
        author_groups.apply_author_group_exposure([target], [conversation])
    )

    assert annotated["within_family_author_group_key"] is None
    assert annotated["author_group_exposure_status"] == "identity_group_conflicting"
    assert annotated["preliminary_within_family_held_out_eligibility"] is False
    assert (
        "target_author_identity_conflicting"
        in annotated["preliminary_within_family_held_out_exclusion_reasons"]
    )
    assert (
        "within_family_identity_group_conflicting"
        in annotated["preliminary_within_family_held_out_exclusion_reasons"]
    )


def test_phase1_2_never_assigns_or_performs_an_author_group_split() -> None:
    result = author_groups.apply_author_group_exposure(
        [
            _target("one", "target-one"),
            _target("two", "target-two"),
        ],
        [_conversation("one"), _conversation("two")],
    )

    assert result["author_group_split_performed"] is False
    assert result["crosstab"]["author_group_split_performed"] is False
    assert all(
        group["author_group_split_assignment"] is None
        for group in result["within_family_author_groups"]
    )
    assert all(
        group["cross_family_author_group_split_assignment"] is None
        for group in result["cross_family_author_groups"]
    )


def test_module_has_only_standard_library_imports() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        str(node.module).split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module != "__future__"
    )

    assert imported_roots <= {
        "collections",
        "copy",
        "hashlib",
        "hmac",
        "json",
        "typing",
    }

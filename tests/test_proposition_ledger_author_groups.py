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
) -> dict[str, Any]:
    return {
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
        )
        == []
    )


def test_identity_conflict_is_explicit_and_not_grouped() -> None:
    conversation = _conversation("conflict", scheme="domain-a", principal="key-a")
    target = _target(
        "conflict", "target", scheme="domain-b", principal="same-or-not-inferred"
    )
    annotated = _one_target(
        author_groups.apply_author_group_exposure([target], [conversation])
    )

    assert annotated["within_family_author_group_key"] is None
    assert annotated["author_group_exposure_status"] == "identity_group_conflicting"
    assert annotated["preliminary_within_family_held_out_eligibility"] is False
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

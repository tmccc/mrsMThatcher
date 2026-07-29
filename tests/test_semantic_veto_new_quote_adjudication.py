"""Focused tests for targeted new-quote semantic-veto adjudication."""

from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path

import pytest

import semantic_veto_new_quote_adjudication as target


def _isolated_adjudication_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Return a temporary source path accepted by the compiler path binder."""
    monkeypatch.setattr(target, "ROOT", tmp_path)
    return tmp_path / "new_quote_pair_adjudications.json"


def test_new_quote_contract_is_source_grounded_and_narrow() -> None:
    packet = target.load_target_packet()
    contract = target.build_new_quote_contract(packet)
    assert contract["quote_id"] == target.TARGET_QUOTE_ID
    assert contract["dominant_proposition"] == packet["intended_argument"]
    assert contract["thatcher_attribution_status"] == "confirmed_thatcher"
    assert contract["claim_type"] == "abstract_principle"
    assert contract["neutral_portrait_allowed"] is True
    assert contract["visually_required_entities"] == []
    assert contract["visually_required_relationships"] == []
    assert contract["visual_event_requirement"] is None
    assert contract["visual_period_requirement"] is None


def test_credential_project_root_is_explicit_and_does_not_copy_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MRS_MTHATCHER_CREDENTIAL_PROJECT_ROOT", "/example/provider-project"
    )
    assert target.credential_project_root() == Path("/example/provider-project")


def test_new_quote_contract_fails_closed_for_concrete_named_entity() -> None:
    packet = copy.deepcopy(target.load_target_packet())
    packet["entities"].append("Mikhail Gorbachev")
    with pytest.raises(
        target.NewQuoteAdjudicationError,
        match="concrete named entities",
    ):
        target.build_new_quote_contract(packet)


def test_current_manifest_has_complete_target_coverage_and_valid_sources() -> None:
    manifest = target.read_json(target.BASE_MANIFEST)
    audit = target.validate_compiled_manifest(manifest, strict=True)
    assert (audit["quote_count"], audit["image_count"]) == (611, 91)
    assert manifest["quote_pair_coverage"][target.TARGET_QUOTE_ID][
        "not_adjudicated_count"
    ] == 0
    assert manifest["quote_pair_coverage"][target.TARGET_QUOTE_ID][
        "allow_count"
    ] == 91


def test_deterministic_plan_covers_all_images_without_inventing_residuals() -> None:
    contract = target.build_new_quote_contract(target.load_target_packet())
    images = target.load_image_contracts()
    decided, residual = target.prepare_pairs(contract, images)
    assert len(images) == 91
    assert len(decided) + len(residual) == 91
    assert {row["image_hash"] for row in decided} | {
        row["image_hash"] for row in residual
    } == set(images)
    assert all(row["decision"] in {"allow", "veto"} for row in decided)
    assert all(row["deterministic_decision"] == "unknown" for row in residual)


def _all_allow_records() -> list[dict[str, object]]:
    contract = target.build_new_quote_contract(target.load_target_packet())
    records = []
    for image in target.load_image_contracts().values():
        records.append(
            target.final_record(
                contract,
                image,
                decision="allow",
                basis="test_only",
                rationale="No affirmative material contradiction was established.",
                confidence="high",
                contradiction_types=[],
                provider_first=None,
                provider_second=None,
            )
        )
    return records


def _transition_base() -> dict[str, object]:
    """Reconstruct the pre-transition matrix for isolated compiler tests."""
    base = copy.deepcopy(target.read_json(target.BASE_MANIFEST))
    base["pairs"] = {
        key: row
        for key, row in base["pairs"].items()
        if row["quote_id"] != target.TARGET_QUOTE_ID
    }
    base["adjudicated_unknown_pairs"] = {
        key: row
        for key, row in base["adjudicated_unknown_pairs"].items()
        if row["quote_id"] != target.TARGET_QUOTE_ID
    }
    coverage_records = [
        *base["pairs"].values(),
        *(
            {**row, "decision": "unknown", "basis": row.get("reason")}
            for row in base["adjudicated_unknown_pairs"].values()
        ),
    ]
    base.update(
        target.semantic_pair_coverage(
            set(base["quote_pair_coverage"]),
            coverage_records,
            set(base["image_hashes"]),
        )
    )
    counts = Counter(row["decision"] for row in base["pairs"].values())
    base["pair_count"] = len(base["pairs"])
    base["resolved_pair_count"] = len(base["pairs"])
    base["allow_count"] = counts["allow"]
    base["veto_count"] = counts["veto"]
    base["source_file_hashes"].pop("new_quote_pair_adjudications", None)
    return base


def test_compile_changes_only_target_missing_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _transition_base()
    records = _all_allow_records()
    source = _isolated_adjudication_path(tmp_path, monkeypatch)
    try:
        target.atomic_write_json(source, {"records": records})
        result = target.compile_manifest(base, records, adjudication_path=source)
    finally:
        source.unlink(missing_ok=True)
    preservation = target.verify_prior_entries(base, result)
    assert preservation["prior_entries_byte_identical"] is True
    coverage = result["quote_pair_coverage"][target.TARGET_QUOTE_ID]
    assert coverage["allow_count"] == 91
    assert coverage["not_adjudicated_count"] == 0
    assert result["total_authorised_pair_count"] == 55_601
    assert result["not_adjudicated_pair_count"] == (
        base["not_adjudicated_pair_count"] - 91
    )


def test_compile_rejects_attempt_to_overwrite_existing_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _transition_base()
    records = _all_allow_records()
    existing = next(iter(base["pairs"].values()))
    records[0]["image_hash"] = existing["image_hash"]
    records[0]["source_pair_id"] = target.pair_id(
        target.TARGET_QUOTE_ID, existing["image_hash"]
    )
    source = _isolated_adjudication_path(tmp_path, monkeypatch)
    try:
        target.atomic_write_json(source, {"records": records})
        # Duplicate image coverage is rejected before any merge can occur.
        with pytest.raises(target.NewQuoteAdjudicationError):
            target.compile_manifest(base, records, adjudication_path=source)
    finally:
        source.unlink(missing_ok=True)


def test_structured_unknown_remains_unknown_not_allow() -> None:
    contract = target.build_new_quote_contract(target.load_target_packet())
    image = next(iter(target.load_image_contracts().values()))
    row = target.final_record(
        contract,
        image,
        decision="adjudicated_unknown",
        basis="provider_uncertain",
        rationale="The source-grounded image meaning remains ambiguous.",
        confidence="low",
        contradiction_types=["insufficient_evidence"],
        provider_first=None,
        provider_second=None,
    )
    output = target.manifest_unknown_row(row)
    assert output["adjudication_status"] == "unknown"
    assert "decision" not in output
    assert output["structured_adjudication"]["policy_version"] == (
        target.ATTRIBUTION_CLEANED_V3_POLICY_VERSION
    )


def test_deterministic_allow_sample_is_repeatable() -> None:
    records = _all_allow_records()
    first = target.deterministic_allow_sample(records)
    second = target.deterministic_allow_sample(records)
    assert first == second
    assert len(first) >= 10
    assert all(row["decision"] == "allow" for row in first)


def test_provider_items_use_existing_typed_schema_without_tools() -> None:
    contract = target.build_new_quote_contract(target.load_target_packet())
    images = target.load_image_contracts()
    _decided, residual = target.prepare_pairs(contract, images)
    items = target.make_provider_items(
        contract, images, residual[:1], second_pass=False
    )
    assert len(items) == 1
    assert items[0]["schema"]["type"] == "object"
    assert "Use decision=allow" in str(items[0]["prompt"])
    assert "Do not use search, tools" in str(items[0]["prompt"])


def test_two_normalised_literalism_vetoes_resolve_to_allow() -> None:
    contract = target.build_new_quote_contract(target.load_target_packet())
    image = next(
        row
        for row in target.load_image_contracts().values()
        if row.get("event")
    )
    pid = target.pair_id(contract["quote_id"], image["image_hash"])
    residual = [
        {
            "pair_id": pid,
            "quote_id": contract["quote_id"],
            "image_hash": image["image_hash"],
            "image_id": image["image_id"],
        }
    ]
    provider = {
        "pair_id": pid,
        "quote_id": contract["quote_id"],
        "image_id": image["image_id"],
        "decision": "veto",
        "confidence": "high",
        "materially_misleading": True,
        "relationship_supported": "not_required",
        "contradiction_types": ["generic_theme_only"],
        "dominant_visual_message": image["dominant_visual_story"],
        "reason": "The image depicts a specific unrelated event.",
        "deterministic_contradictions": [],
        "final_decision": "veto",
        "final_reason": "model_veto_or_uncertainty",
    }
    records = target.resolve_provider_rows(
        contract,
        {image["image_hash"]: image},
        residual,
        {pid: provider},
        {pid: provider},
    )
    assert records[0]["decision"] == "allow"
    assert records[0]["confidence"] == "medium"
    assert records[0]["provider_first_pass"]["decision"] == "veto"
    assert records[0]["provider_first_pass"]["final_decision"] == "allow"


def test_complete_manifest_build_is_byte_identical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _transition_base()
    records = _all_allow_records()
    source = _isolated_adjudication_path(tmp_path, monkeypatch)
    try:
        target.atomic_write_json(source, {"records": records})
        first = target.compile_manifest(base, records, adjudication_path=source)
        second = target.compile_manifest(base, records, adjudication_path=source)
    finally:
        source.unlink(missing_ok=True)
    assert json.dumps(first, indent=2, sort_keys=True) == json.dumps(
        second, indent=2, sort_keys=True
    )

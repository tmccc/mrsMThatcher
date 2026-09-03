from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

import mrsMThatcher2 as bot
from historical_context_formatter import format_context_reply
from shadow_lifecycle import (
    ShadowLifecycleError,
    lifecycle_decision_schedule,
    load_lifecycle_register,
    validate_lifecycle_register,
)


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "semantic_alignment_research" / "quote_research_full_001"
RUNTIME_ELIGIBILITY_MANIFEST = (
    ROOT
    / "semantic_alignment_research"
    / "quote_attribution_cleanup_001"
    / "deployment_candidate"
    / "runtime_eligible_quote_manifest.json"
)


def _configure_runtime_eligibility_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "HISTORICAL_CONTEXT_RESEARCH_DIR", RESEARCH)
    monkeypatch.setattr(
        bot,
        "COMPLETED_QUOTE_RESEARCH_FILE",
        RESEARCH / "research_packets.json",
    )
    monkeypatch.setattr(bot, "LINES_FILE", ROOT / "mrsMThatcher.txt")
    monkeypatch.setattr(
        bot,
        "RUNTIME_ELIGIBLE_QUOTE_MANIFEST_FILE",
        RUNTIME_ELIGIBILITY_MANIFEST,
    )


def test_lifecycle_register_has_the_three_evidence_led_states() -> None:
    value = load_lifecycle_register(ROOT / "shadow_feature_lifecycle.json")
    states = {
        feature["feature_name"]: feature["current_state"]
        for feature in value["features"]
    }
    assert states == {
        "generated_image_identity_policy": "suspended",
        "hybrid_reply_retrieval": "offline_only",
        "original_editorial_selector": "promoted",
    }


def test_lifecycle_register_rejects_invalid_states_fail_closed() -> None:
    value = json.loads((ROOT / "shadow_feature_lifecycle.json").read_text(encoding="utf-8"))
    value["features"][0]["current_state"] = "active"
    with pytest.raises(ShadowLifecycleError, match="unsupported lifecycle state"):
        validate_lifecycle_register(value)


def test_lifecycle_register_exposes_overdue_decision_dates() -> None:
    value = json.loads((ROOT / "shadow_feature_lifecycle.json").read_text(encoding="utf-8"))
    value["features"][0]["next_decision_date"] = "2026-07-18"
    schedule = lifecycle_decision_schedule(value, as_of=date(2026, 7, 19))
    assert schedule[0] == {
        "feature_name": "hybrid_reply_retrieval",
        "next_decision_date": "2026-07-18",
        "decision_overdue": True,
        "days_overdue": 1,
    }


def test_uncertain_packet_remains_available_to_explicit_context_formatter() -> None:
    packets = json.loads((RESEARCH / "research_packets.json").read_text(encoding="utf-8"))["items"]
    uncertain = next(
        row for row in packets.values() if row.get("verification_status") == "unverified"
    )
    result = format_context_reply(uncertain)
    assert result is not None
    assert result["verification_label"] == "Exact wording not verified"
    assert "Verification: Exact wording not verified" in result["text"]


def test_runtime_regular_post_gate_uses_all_611_attribution_eligible_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_runtime_eligibility_assets(monkeypatch)
    eligible = bot.completed_research_quote_hashes()
    assert len(eligible) == 611


def test_exact_65_withdrawn_wording_exclusions_are_restored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = json.loads(
        (ROOT / "tests/fixtures/restored_regular_post_quote_ids.json").read_text(
            encoding="utf-8"
        )
    )
    restored = set(fixture["quote_ids"])
    source_ids = set(
        bot.current_quote_hashes_by_line(
            (ROOT / "mrsMThatcher.txt").read_text(encoding="utf-8").splitlines()
        ).values()
    )
    assert fixture["quote_count"] == len(restored) == 65
    assert restored <= source_ids
    _configure_runtime_eligibility_assets(monkeypatch)
    assert restored <= bot.completed_research_quote_hashes()

    packets = json.loads((RESEARCH / "research_packets.json").read_text(encoding="utf-8"))["items"]
    metadata_fields = (
        "quote_id", "quote_text", "verified_text", "verification_status",
        "research_confidence", "editorial_guidance", "source", "source_event",
        "stable_locator",
    )
    metadata = {
        quote_id: {field: packets[quote_id].get(field) for field in metadata_fields}
        for quote_id in sorted(restored)
    }
    metadata_hash = hashlib.sha256(
        json.dumps(
            metadata, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    assert metadata_hash == fixture["uncertainty_metadata_sha256"]
    assert dict(sorted(Counter(
        str(packets[quote_id].get("verification_status")) for quote_id in restored
    ).items())) == fixture["verification_status_counts"]
    assert dict(sorted(Counter(
        str(packets[quote_id].get("research_confidence")) for quote_id in restored
    ).items())) == fixture["research_confidence_counts"]


def test_restored_611_quote_cycle_histories_do_not_false_exhaust_or_write_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_runtime_eligibility_assets(monkeypatch)
    monkeypatch.setattr(bot, "QUOTE_ANALYSIS_FILE", ROOT / "quote_analysis.json")
    eligible = bot.completed_research_quote_hashes()
    assert len(eligible) == 611

    history_path = tmp_path / "lines_used.json"
    bot.save_used_set(history_path, set())
    monkeypatch.setattr(bot, "LINES_USED_FILE", history_path)
    receipt_sentinels: list[tuple[Path, bytes]] = []
    for attribute in (
        "REGULAR_POST_RECEIPT_FILE",
        "MEME_POST_RECEIPT_FILE",
        "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE",
        "CONFIRMED_REPLY_RECEIPT_FILE",
    ):
        path = tmp_path / f"{attribute.lower()}.json"
        path.write_bytes(f"sentinel:{attribute}".encode())
        monkeypatch.setattr(bot, attribute, path)
        receipt_sentinels.append((path, path.read_bytes()))

    fresh_candidates = bot.quote_candidates_for_current_cycle(set())
    selectable = {row["quote_hash"] for row in fresh_candidates}
    assert selectable
    assert selectable <= eligible

    partial_used = set(sorted(eligible)[:100])
    bot.save_used_set(history_path, partial_used)
    loaded_partial = bot.load_used_set(history_path)
    partial_before = set(loaded_partial)
    partial_candidates = bot.quote_candidates_for_current_cycle(loaded_partial)
    assert loaded_partial == partial_before
    assert partial_candidates
    assert all(row["quote_hash"] in eligible - partial_before for row in partial_candidates)

    final_unused = sorted(selectable)[0]
    almost_used = eligible - {final_unused}
    bot.save_used_set(history_path, almost_used)
    loaded_almost = bot.load_used_set(history_path)
    almost_before = set(loaded_almost)
    almost_candidates = bot.quote_candidates_for_current_cycle(loaded_almost)
    assert loaded_almost == almost_before
    assert {row["quote_hash"] for row in almost_candidates} == {final_unused}

    bot.save_used_set(history_path, eligible)
    loaded_exhausted = bot.load_used_set(history_path)
    exhausted_candidates = bot.quote_candidates_for_current_cycle(loaded_exhausted)
    assert loaded_exhausted == set()
    assert exhausted_candidates
    assert {row["quote_hash"] for row in exhausted_candidates} == selectable

    assert all(path.read_bytes() == original for path, original in receipt_sentinels)


def test_legacy_live_reply_configuration_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = {
        "enabled": True,
        "mode": "shadow",
        "index_path": "semantic_alignment_research/hybrid_reply_retrieval_001",
        "maximum_results": 5,
        "semantic_candidate_count": 20,
        "lexical_candidate_count": 20,
        "query_timeout_ms": 1000,
        "maximum_shadow_history": 5000,
        "fail_open": True,
    }
    config = tmp_path / "mrsMThatcher.local.json"
    config.write_text(
        json.dumps({"reply_strategy": {"enabled": True, "hybrid_retrieval": legacy}})
    )
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", config)

    with pytest.raises(bot.LocalConfigError, match="retired reply_strategy V1"):
        bot.apply_local_config()


def test_no_allowed_image_quote_is_incomplete_manifest_coverage_not_91_vetoes() -> None:
    quote_id = "67eacce6d9e102d4cf8a316451f9b8b9c095fdc6d0cffffb5a2d445e43b3d44d"
    image_hash = "f271019f2226396d8fdbc5297b968240d9654591b94f65778d7a84d2bc16a63a"
    candidate = json.loads(
        (
            ROOT
            / "semantic_alignment_research/quote_image_metadata_remediation_001/"
            "candidate_manifest_v3.json"
        ).read_text(encoding="utf-8")
    )
    rows = [
        row for row in candidate["records"].values() if row.get("quote_id") == quote_id
    ]
    assert len(rows) == 35
    assert sum(row["decision"] == "veto" for row in rows) == 10
    assert sum(row["decision"] == "unknown" for row in rows) == 25
    assert 91 - len(rows) == 56
    assert not any(row.get("image_hash") == image_hash for row in rows)

    matching_images = []
    contracts = (
        ROOT
        / "semantic_alignment_research/quote_image_metadata_remediation_001/"
        "image_contracts_v3.jsonl"
    )
    for line in contracts.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("image_hash") == image_hash:
            matching_images.append(row)
    assert len(matching_images) == 1
    assert "Mikhail Gorbachev" in matching_images[0]["known_participants"]

    manifest = json.loads(
        (
            ROOT
            / "semantic_alignment_research/quote_attribution_cleanup_001/"
            "deployment_candidate/material_veto_v3_shadow_manifest.json"
        ).read_text(encoding="utf-8")
    )
    coverage = manifest["quote_pair_coverage"][quote_id]
    assert coverage == {
        "adjudicated_unknown_count": 27,
        "allow_count": 0,
        "authorised_image_count": 91,
        "complete_pair_coverage": False,
        "fully_resolved_pair_coverage": False,
        "global_no_safe_image": False,
        "not_adjudicated_count": 54,
        "observed_pair_count": 37,
        "resolved_pair_count": 10,
        "veto_count": 10,
    }
    assert manifest["quote_has_allowed_candidate"][quote_id] is None
    pair_key = f"{quote_id}:{image_hash}"
    assert pair_key not in manifest["pairs"]
    assert pair_key not in manifest["adjudicated_unknown_pairs"]

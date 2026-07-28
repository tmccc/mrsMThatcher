from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import quote_attribution_cleanup as cleanup


def _configure_runtime_eligibility_assets(
    monkeypatch: pytest.MonkeyPatch,
):
    import mrsMThatcher2 as bot

    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_RESEARCH_DIR",
        cleanup.RESEARCH_RUN,
    )
    monkeypatch.setattr(
        bot,
        "COMPLETED_QUOTE_RESEARCH_FILE",
        cleanup.RESEARCH_RUN / "research_packets.json",
    )
    monkeypatch.setattr(bot, "LINES_FILE", cleanup.ROOT / cleanup.SOURCE_NAME)
    monkeypatch.setattr(
        bot,
        "RUNTIME_ELIGIBLE_QUOTE_MANIFEST_FILE",
        cleanup.DEFAULT_RUN
        / "deployment_candidate"
        / "runtime_eligible_quote_manifest.json",
    )
    return bot


def test_structured_attribution_partition_is_exact() -> None:
    rows = cleanup.load_attribution_targets(cleanup.DEFAULT_REMEDIATION)
    assert len(rows) == 13
    assert sum(row["classification"] == "confirmed_non_thatcher_or_misattributed" for row in rows) == 9
    assert sum(row["classification"] == "canonical_speaker_not_grounded" for row in rows) == 4
    assert len({row["quote_id"] for row in rows}) == 13


def test_relative_remediation_directory_is_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(cleanup.ROOT)
    rows = cleanup.load_attribution_targets(Path("semantic_alignment_research/quote_image_metadata_remediation_001"))
    assert len(rows) == 13


def test_all_targets_map_uniquely_to_exact_source_records() -> None:
    rows = cleanup.load_attribution_targets(cleanup.DEFAULT_REMEDIATION)
    before = cleanup.DEFAULT_RUN / "mrsMThatcher_before.txt"
    records, mapped = cleanup.map_targets(before.read_bytes(), rows)
    assert len(records) == 633
    assert len(mapped) == 13
    assert all(row["exact_quote_text"] == records[row["physical_line"] - 1]["text"] for row in mapped)


def test_ambiguous_target_mapping_fails_closed() -> None:
    text = "A test quotation."
    target = {
        "quote_id": cleanup.quote_id(text),
        "exact_quote_text": text,
    }
    with pytest.raises(cleanup.CleanupError, match="does not map uniquely"):
        cleanup.map_targets(f"{text}\n{text}\n".encode(), [target])


def test_candidate_deletion_preserves_retained_bytes_order_and_newline() -> None:
    before = (cleanup.DEFAULT_RUN / "mrsMThatcher_before.txt").read_bytes() if (
        cleanup.DEFAULT_RUN / "mrsMThatcher_before.txt"
    ).exists() else (cleanup.ROOT / cleanup.SOURCE_NAME).read_bytes()
    rows = cleanup.load_attribution_targets(cleanup.DEFAULT_REMEDIATION)
    _, mapped = cleanup.map_targets(before, rows)
    after = cleanup.build_after_payload(before, mapped)
    remove = {row["quote_id"] for row in rows}
    expected = b"".join(row["raw"] for row in cleanup.source_records(before) if row["quote_id"] not in remove)
    assert after == expected
    assert after.endswith(b"\n") == before.endswith(b"\n")
    assert len(cleanup.source_records(after)) == 620
    assert len({row["quote_id"] for row in cleanup.source_records(after)}) == 619


def test_atomic_write_replaces_complete_file(tmp_path: Path) -> None:
    path = tmp_path / "source.txt"
    path.write_bytes(b"before\n")
    cleanup.atomic_write_bytes(path, b"after\n")
    assert path.read_bytes() == b"after\n"
    assert not list(tmp_path.glob(".source.txt.*"))


def test_current_reduced_source_or_before_snapshot_has_expected_identity_partition() -> None:
    source = cleanup.ROOT / cleanup.SOURCE_NAME
    ids = {row["quote_id"] for row in cleanup.source_records(source.read_bytes())}
    targets = {row["quote_id"] for row in cleanup.load_attribution_targets(cleanup.DEFAULT_REMEDIATION)}
    assert len(ids) in {632, 619}
    assert (len(ids & targets) == 13) if len(ids) == 632 else not (ids & targets)


def test_five_unresolved_records_remain_source_retained_and_ineligible() -> None:
    status = cleanup.read_json(cleanup.RESEARCH_RUN / "final_unresolved/final_research_status.json")
    unresolved = set(status["unresolved_quote_ids"])
    source_ids = {row["exact_quote_id"] for row in cleanup.source_records((cleanup.ROOT / cleanup.SOURCE_NAME).read_bytes())}
    contracts = cleanup.jsonl(cleanup.DEFAULT_REMEDIATION / "quote_contracts_v3.jsonl")
    confirmed = {row["quote_id"] for row in contracts if row.get("thatcher_attribution_status") == "confirmed_thatcher"}
    assert len(unresolved) == 5
    assert unresolved <= source_ids
    assert not (unresolved & confirmed)


def test_five_established_whitespace_aliases_are_preserved() -> None:
    rows = cleanup.source_records((cleanup.ROOT / cleanup.SOURCE_NAME).read_bytes())
    contracts = cleanup.jsonl(cleanup.DEFAULT_REMEDIATION / "quote_contracts_v3.jsonl")
    confirmed = {row["quote_id"] for row in contracts if row.get("thatcher_attribution_status") == "confirmed_thatcher"}
    aliases = {
        row["quote_id"]: row["exact_quote_id"]
        for row in rows
        if row["exact_quote_id"] in confirmed and row["quote_id"] != row["exact_quote_id"]
    }
    assert len(aliases) == 5


def test_deployment_candidate_contains_only_confirmed_active_quotes_when_built() -> None:
    path = cleanup.DEFAULT_RUN / "deployment_candidate/active_quote_manifest.json"
    if not path.exists():
        pytest.skip("deployment candidate not built yet")
    active = cleanup.read_json(path)
    shadow = cleanup.read_json(cleanup.DEFAULT_RUN / "deployment_candidate/semantic_veto_shadow_manifest.json")
    tombstones = cleanup.read_json(cleanup.DEFAULT_RUN / "deployment_candidate/attribution_exclusion_tombstones.json")
    active_ids = set(active["active_quote_ids"])
    removed_ids = {row["quote_id"] for row in tombstones["records"]}
    assert len(active_ids) == 613
    assert not (active_ids & removed_ids)
    assert shadow["quote_count"] == 613
    assert {row["quote_id"] for row in shadow["pairs"].values()} == active_ids
    assert all(
        "non_thatcher_speaker_portrait_substitution" not in row.get("veto_reason_codes", [])
        for row in shadow["pairs"].values()
    )


def test_unknown_category_is_total_and_explicit() -> None:
    manifest = cleanup.read_json(cleanup.DEFAULT_REMEDIATION / "candidate_manifest_v3.json")
    unknown = [row for row in manifest["records"].values() if row["decision"] == "unknown"]
    counts = {}
    for row in unknown:
        category = cleanup.unknown_category(row)
        counts[category] = counts.get(category, 0) + 1
    assert sum(counts.values()) == len(unknown) == 272
    assert "canonical_speaker_not_grounded" in counts


def test_incomplete_pair_coverage_is_not_reported_as_global_no_safe_image() -> None:
    quote_id = "a" * 64
    image_hashes = {f"{value:064x}" for value in range(91)}
    records = [
        {
            "quote_id": quote_id,
            "image_hash": f"{0:064x}",
            "decision": "veto",
            "pair_id": "b" * 64,
        },
        {
            "quote_id": quote_id,
            "image_hash": f"{1:064x}",
            "decision": "unknown",
            "pair_id": "c" * 64,
            "basis": "source_evidence_gap",
        },
    ]

    result = cleanup.semantic_pair_coverage({quote_id}, records, image_hashes)

    assert result["quote_has_allowed_candidate"][quote_id] is None
    assert result["quotes_without_allowed_candidate"] == 0
    assert result["adjudicated_unknown_pair_count"] == 1
    assert result["not_adjudicated_pair_count"] == 89
    assert result["quote_pair_coverage"][quote_id]["global_no_safe_image"] is False


def test_complete_all_veto_coverage_is_reported_as_global_no_safe_image() -> None:
    quote_id = "a" * 64
    image_hashes = {f"{value:064x}" for value in range(91)}
    records = [
        {
            "quote_id": quote_id,
            "image_hash": image_hash,
            "decision": "veto",
            "pair_id": hashlib.sha256(f"{quote_id}:{image_hash}".encode()).hexdigest(),
        }
        for image_hash in sorted(image_hashes)
    ]

    result = cleanup.semantic_pair_coverage({quote_id}, records, image_hashes)

    assert result["quote_has_allowed_candidate"][quote_id] is False
    assert result["quotes_without_allowed_candidate"] == 1
    assert result["quotes_without_allowed_candidate_ids"] == [quote_id]
    assert result["not_adjudicated_pair_count"] == 0
    assert result["quote_pair_coverage"][quote_id]["global_no_safe_image"] is True


def test_production_history_uses_quote_hashes_not_shifted_line_indices() -> None:
    source = (cleanup.ROOT / "mrsMThatcher2.py").read_text(encoding="utf-8")
    assert "lines_used.add(quote_hash)" in source
    assert "def normalise_quote_used_hashes" in source


def test_production_regular_selector_has_completed_research_gate() -> None:
    source = (cleanup.ROOT / "mrsMThatcher2.py").read_text(encoding="utf-8")
    assert "def completed_research_quote_hashes" in source
    assert "research_ineligible_hashes" in source
    assert "excluded_quote_hashes = set(excluded_quote_hashes or set()).union(research_ineligible_hashes)" in source


def test_completed_research_gate_excludes_all_attribution_ineligible_source_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = _configure_runtime_eligibility_assets(monkeypatch)
    completed = bot.completed_research_quote_hashes()
    source_hashes = {
        bot.quote_text_hash(row["text"])
        for row in cleanup.source_records((cleanup.ROOT / cleanup.SOURCE_NAME).read_bytes())
    }
    unresolved = set(
        cleanup.read_json(cleanup.RESEARCH_RUN / "final_unresolved/final_research_status.json")["unresolved_quote_ids"]
    )
    false_positive_ids = {
        "7f75c4d086fb67b0e54d9d63dbe470dc6f9f929aee00ce4a02d01bbc9c8d4646",
        "cf7a03be1c6e34efbcfec0cc8010544e2deab777a05cb0193d237814244f5c8e",
        "8c70978a89ef43e405dbc7eb0bb9751d9dbe631d63d9834ccf3dfde51a4a971c",
    }
    assert len(source_hashes & completed) == 611
    assert len(source_hashes - completed) == 8
    assert unresolved | false_positive_ids == source_hashes - completed


def test_test_mode_research_gate_still_requires_complete_integrity_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bot = _configure_runtime_eligibility_assets(monkeypatch)
    monkeypatch.setattr(bot, "TEST_MODE", True)

    assert len(bot.completed_research_quote_hashes()) == 611
    monkeypatch.setattr(
        bot,
        "RUNTIME_ELIGIBLE_QUOTE_MANIFEST_FILE",
        tmp_path / "missing-runtime-eligibility.json",
    )
    with pytest.raises(RuntimeError, match="manifest unavailable"):
        bot.completed_research_quote_hashes()


def test_no_network_or_provider_code_in_cleanup_tool() -> None:
    source = (cleanup.ROOT / "quote_attribution_cleanup.py").read_text(encoding="utf-8")
    forbidden = ("requests.", "httpx.", "google.genai", "openai", "anthropic", "xai")
    assert not any(token in source.lower() for token in forbidden)


def test_validator_queries_only_real_harness_columns(tmp_path: Path) -> None:
    import quote_image_selection_harness as harness

    database_path = tmp_path / "simulation.sqlite3"
    connection = harness.initialise_database(database_path)
    connection.close()
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(simulation_events)")}
    connection.close()
    assert {"quote_id", "production_image_hash", "mode"} <= columns
    source = (cleanup.ROOT / "quote_attribution_cleanup.py").read_text(encoding="utf-8")
    assert "SELECT quote_id,production_image_hash FROM simulation_events" in source


def test_frozen_current_winner_manifest_covers_all_reduced_active_quotes() -> None:
    path = cleanup.ROOT / "semantic_alignment_research/relation_aware_semantic_veto_002/production_top8_pair_candidates_v2_postrun_corrected.json"
    rows = cleanup.read_json(path)["records"]
    active_path = cleanup.DEFAULT_RUN / "deployment_candidate/active_quote_manifest.json"
    if not active_path.exists():
        pytest.skip("deployment candidate not built yet")
    active = set(cleanup.read_json(active_path)["active_quote_ids"])
    assert {row["quote_id"] for row in rows if row["quote_id"] in active} == active


def test_active_quote_analysis_migration_preserves_all_retained_analysis_payloads() -> None:
    source = (cleanup.ROOT / cleanup.SOURCE_NAME).read_bytes()
    original = cleanup.read_json(cleanup.ROOT / "quote_analysis.json")
    tombstones = cleanup.read_json(
        cleanup.DEFAULT_RUN / "deployment_candidate/attribution_exclusion_tombstones.json"
    )
    tombstone_ids = {row["quote_id"] for row in tombstones["records"]}
    migrated, audit = cleanup.build_migrated_quote_analysis(source, original, tombstone_ids)
    current_ids = {row["quote_id"] for row in cleanup.source_records(source)}
    assert set(migrated["items"]) == current_ids
    assert len(migrated["items"]) == 619
    assert len(migrated["line_index"]) == 620
    assert migrated["source"]["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert all(
        migrated["items"][qid]["analysis"] == original["items"][qid]["analysis"]
        for qid in current_ids
    )
    assert audit["analysis_payloads_preserved"] == 619


def test_live_quote_analysis_and_overrides_match_cleaned_line_map() -> None:
    source = (cleanup.ROOT / cleanup.SOURCE_NAME).read_bytes()
    records = cleanup.source_records(source)
    analysis = cleanup.read_json(cleanup.ROOT / "quote_analysis.json")
    overrides = cleanup.read_json(cleanup.ROOT / "quote_analysis_overrides.json")
    expected_lines: dict[str, list[int]] = {}
    for row in records:
        expected_lines.setdefault(row["quote_id"], []).append(row["physical_line"])
    assert analysis["source"]["source_sha256"] == hashlib.sha256(source).hexdigest()
    assert len(analysis["items"]) == 619
    assert len(analysis["line_index"]) == 620
    for qid, override in overrides["quote_overrides"].items():
        assert override["expected_line_numbers"] == expected_lines[qid]


def test_prepared_v3_shadow_manifest_covers_current_cycle_fail_closed() -> None:
    path = cleanup.DEFAULT_RUN / "deployment_candidate/material_veto_v3_shadow_manifest.json"
    if not path.is_file():
        pytest.skip("digest021 v3 shadow candidate not prepared yet")
    from semantic_alignment.quote_image_semantic_veto import ShadowRuntime, validate_compiled_manifest

    manifest = cleanup.read_json(path)
    audit = validate_compiled_manifest(manifest, strict=True)
    assert audit["quote_count"] == 611
    assert audit["pair_count"] == 22_157
    config = {
        "enabled": True,
        "mode": "shadow",
        "manifest_path": str(path.relative_to(cleanup.ROOT)),
        "fail_open": True,
        "record_best_allowed_alternative": True,
        "maximum_shadow_history": 10_000,
    }
    runtime = ShadowRuntime.load(
        cleanup.ROOT,
        config,
        verify_source_hashes=True,
        enable_history=False,
    )
    assert runtime.available is True
    assert runtime.status == "allow"
    eligibility = cleanup.read_json(
        cleanup.DEFAULT_RUN / "deployment_candidate/runtime_eligible_quote_manifest.json"
    )
    expected_runtime_ids = set(eligibility["runtime_eligible_quote_ids"])
    runtime = ShadowRuntime.load(
        cleanup.ROOT,
        config,
        verify_source_hashes=True,
        enable_history=False,
        expected_runtime_quote_ids=expected_runtime_ids,
    )
    assert runtime.available is True
    assert runtime.status == "allow"
    pair_quote_ids = {
        row["quote_id"]
        for row in [
            *manifest["pairs"].values(),
            *manifest["adjudicated_unknown_pairs"].values(),
        ]
    }
    assert set(manifest["quote_has_allowed_candidate"]) - pair_quote_ids == set()
    new_quote_id = (
        "0a67f403a7ac02347e43791d2daf3057aabdcfd64b62edbe1b3484a3a4b66729"
    )
    assert manifest["quote_has_allowed_candidate"][new_quote_id] is True
    assert manifest["quote_pair_coverage"][new_quote_id] == {
        "adjudicated_unknown_count": 0,
        "allow_count": 91,
        "authorised_image_count": 91,
        "complete_pair_coverage": True,
        "fully_resolved_pair_coverage": True,
        "global_no_safe_image": False,
        "not_adjudicated_count": 0,
        "observed_pair_count": 91,
        "resolved_pair_count": 91,
        "veto_count": 0,
    }

    stale = ShadowRuntime.load(
        cleanup.ROOT,
        config,
        verify_source_hashes=True,
        enable_history=False,
        expected_runtime_quote_ids=expected_runtime_ids - {next(iter(expected_runtime_ids))},
    )
    assert stale.available is False
    assert stale.status == "manifest_stale"

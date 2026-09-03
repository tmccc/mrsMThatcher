from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import pytest

import semantic_alignment.quote_image_semantic_veto as semantic_veto
from semantic_alignment.io import sha256_file
from semantic_alignment.quote_image_semantic_veto import (
    ATTRIBUTION_CLEANED_V3_POLICY_VERSION,
    ShadowManifestError,
    ShadowHistoryWriter,
    ShadowRuntime,
    compile_shadow_manifest,
    historical_replay,
    read_shadow_history,
    sha256_value,
    shadow_preflight,
    shadow_status,
    summarise_events,
    validate_compiled_manifest,
    validate_shadow_config,
)


PROJECT = Path(__file__).resolve().parents[1]
RUN_DIR = PROJECT / "semantic_alignment_research" / "quote_image_semantic_veto_001"
MANIFEST = RUN_DIR / "shadow" / "material_veto_v2_shadow_manifest.json"


def enabled_config(path: Path = MANIFEST) -> dict:
    return {
        "enabled": True,
        "mode": "shadow",
        "manifest_path": str(path),
        "fail_open": True,
        "record_best_allowed_alternative": True,
        "maximum_shadow_history": 1000,
    }


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_semantic_shadow_history_retains_bounded_tail_across_rotation(tmp_path: Path) -> None:
    writer = ShadowHistoryWriter(tmp_path, maximum_records=2)
    for index in range(3):
        writer.append({
            "event": "quote_image_semantic_veto_shadow",
            "quote_id": str(index),
            "shadow_status": "allow",
        })

    retained = read_shadow_history(tmp_path, maximum=2)
    assert [row["quote_id"] for row in retained] == ["1", "2"]


def test_corrected_source_precedence_and_exact_counts() -> None:
    manifest, audit, output = compile_shadow_manifest(RUN_DIR, strict=True)
    assert output == MANIFEST
    assert audit["valid"] is True
    assert (manifest["pair_count"], manifest["allow_count"], manifest["veto_count"]) == (5862, 5453, 409)
    assert manifest["current_production_winner_count"] == 626
    assert manifest["current_production_winner_allow_count"] == 583
    assert manifest["current_production_winner_veto_count"] == 43
    assert manifest["quotes_with_allowed_candidate"] == 598
    assert manifest["quotes_without_allowed_candidate"] == 28
    assert manifest["critical_ally_enemy_veto_count"] == 3
    assert manifest["eligible_positive_retained_count"] == 5
    assert manifest["unknown_pair_count"] == 0
    assert "postrun_corrected" in manifest["source_run_id"]
    source_names = {key: Path(item["path"]).name for key, item in manifest["source_file_hashes"].items()}
    assert source_names["corrected_final_status"] == "v2_final_status_postrun_corrected.json"
    assert source_names["corrected_evaluation"] == "v2_final_evaluation_postrun_corrected.json"
    assert source_names["correction_audit"] == "v2_postrun_correction_audit.json"
    assert source_names["pair_decisions"] == "pair_judgements_v2_postrun_corrected.json"
    assert source_names["candidate_pair_manifest"] == "production_top8_pair_candidates_v2_postrun_corrected.json"


def test_manifest_compilation_is_deterministic() -> None:
    first, _, _ = compile_shadow_manifest(RUN_DIR, strict=True)
    second, _, _ = compile_shadow_manifest(RUN_DIR, strict=True)
    assert sha256_value(first) == sha256_value(second)


def test_manifest_pair_uniqueness_and_basename_independent_lookup(tmp_path: Path) -> None:
    manifest = load_manifest()
    audit = validate_compiled_manifest(manifest)
    assert audit["source_pair_ids_unique"] is True
    runtime = ShadowRuntime.load(tmp_path, enabled_config(), verify_source_hashes=False, enable_history=False)
    allow = next(row for row in manifest["pairs"].values() if row["decision"] == "allow")
    veto = next(row for row in manifest["pairs"].values() if row["decision"] == "veto")
    assert runtime.pair(allow["quote_id"], allow["image_hash"])["decision"] == "allow"
    assert runtime.pair(veto["quote_id"], veto["image_hash"])["decision"] == "veto"
    assert "basename" not in allow


def test_attribution_cleaned_v3_policy_is_strictly_validated() -> None:
    path = PROJECT / (
        "semantic_alignment_research/quote_attribution_cleanup_001/"
        "deployment_candidate/material_veto_v3_shadow_manifest.json"
    )
    if not path.is_file():
        pytest.skip("attribution-cleaned v3 candidate has not been prepared")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["policy_version"] == ATTRIBUTION_CLEANED_V3_POLICY_VERSION
    audit = validate_compiled_manifest(manifest)
    assert (audit["quote_count"], audit["image_count"]) == (611, 91)
    assert (audit["allow_count"], audit["veto_count"]) == (22_029, 128)
    assert audit["adjudicated_unknown_pair_count"] == 167
    assert audit["not_adjudicated_pair_count"] == 33_277
    assert audit["quotes_with_incomplete_pair_coverage"] == 610
    assert audit["quotes_without_allowed_candidate"] == 0


def test_attribution_cleaned_v3_rejects_old_corpus_counts() -> None:
    path = PROJECT / (
        "semantic_alignment_research/quote_attribution_cleanup_001/"
        "deployment_candidate/material_veto_v3_shadow_manifest.json"
    )
    if not path.is_file():
        pytest.skip("attribution-cleaned v3 candidate has not been prepared")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["quote_count"] = 626
    with pytest.raises(ShadowManifestError, match="v3 manifest quote_count mismatch"):
        validate_compiled_manifest(manifest)


def test_conflicting_pair_decision_is_rejected() -> None:
    manifest = load_manifest()
    key, row = next(iter(manifest["pairs"].items()))
    bad = copy.deepcopy(manifest)
    bad["pairs"][key]["decision"] = "unknown"
    with pytest.raises(ShadowManifestError, match="unknown pair verdict"):
        validate_compiled_manifest(bad)


def test_generated_and_unknown_original_are_nonblocking(tmp_path: Path) -> None:
    runtime = ShadowRuntime.load(tmp_path, enabled_config(), verify_source_hashes=False, enable_history=False)
    quote_id = next(iter(runtime.quote_flags or {}))
    generated = {"basename": "tg_test.png", "image_hash": "a" * 64, "image_source": "generated", "score": 10.0}
    event = runtime.evaluate(quote_hash=quote_id, selected=generated, candidates=[generated])
    assert event["shadow_status"] == "out_of_scope_generated"
    assert event["veto_category"] is None
    assert event["would_veto_production_winner"] is False
    unknown = {"basename": "new.jpg", "image_hash": "b" * 64, "image_source": "original", "score": 10.0}
    event = runtime.evaluate(quote_hash=quote_id, selected=unknown, candidates=[unknown])
    assert event["shadow_status"] == "unknown_unjudged"
    assert event["veto_category"] is None
    assert event["production_selection_changed"] is False


def test_allowed_event_has_no_veto_category(tmp_path: Path) -> None:
    manifest = load_manifest()
    allowed = next(row for row in manifest["pairs"].values() if row["decision"] == "allow")
    selected = {
        "basename": "allowed.jpg",
        "image_hash": allowed["image_hash"],
        "image_source": "original",
        "score": 10.0,
    }
    runtime = ShadowRuntime.load(tmp_path, enabled_config(), verify_source_hashes=False, enable_history=False)
    event = runtime.evaluate(quote_hash=allowed["quote_id"], selected=selected, candidates=[selected])
    assert event["shadow_status"] == "allow"
    assert event["veto_category"] is None
    assert event["alternative_available"] is False


def test_veto_finds_best_allowed_alternative_without_rng(tmp_path: Path) -> None:
    manifest = load_manifest()
    by_quote: dict[str, list[dict]] = {}
    for row in manifest["pairs"].values():
        by_quote.setdefault(row["quote_id"], []).append(row)
    rows = next(rows for rows in by_quote.values() if any(row["decision"] == "veto" for row in rows) and any(row["decision"] == "allow" for row in rows))
    veto = next(row for row in rows if row["decision"] == "veto")
    allows = [row for row in rows if row["decision"] == "allow"][:2]
    selected = {"basename": "selected.jpg", "image_hash": veto["image_hash"], "image_source": "original", "score": 50.0}
    candidates = [selected]
    for index, row in enumerate(allows):
        candidates.append({"basename": f"allow-{index}.jpg", "image_hash": row["image_hash"], "image_source": "original", "score": 40.0 + index})
    runtime = ShadowRuntime.load(tmp_path, enabled_config(), verify_source_hashes=False, enable_history=False)
    rng = random.getstate()
    event = runtime.evaluate(quote_hash=veto["quote_id"], selected=selected, candidates=candidates)
    assert random.getstate() == rng
    assert event["shadow_status"] == "veto"
    assert event["veto_category"] == "selection_error_candidate_available"
    assert event["alternative_available"] is True
    assert event["quote_has_no_allowed_candidate_globally"] is False
    assert event["alternative_image_basename"] == f"allow-{len(allows) - 1}.jpg"
    assert event["score_delta_from_production_winner"] == pytest.approx(40.0 + len(allows) - 1 - 50.0)
    assert event["veto_reason_codes"]


def test_alternative_tie_uses_cloned_production_rng_without_consuming_it(tmp_path: Path) -> None:
    manifest = load_manifest()
    by_quote: dict[str, list[dict]] = {}
    for row in manifest["pairs"].values():
        by_quote.setdefault(row["quote_id"], []).append(row)
    rows = next(rows for rows in by_quote.values() if sum(row["decision"] == "allow" for row in rows) >= 2 and any(row["decision"] == "veto" for row in rows))
    veto = next(row for row in rows if row["decision"] == "veto")
    allows = [row for row in rows if row["decision"] == "allow"][:2]
    selected = {"basename": "selected.jpg", "image_hash": veto["image_hash"], "image_source": "original", "score": 50.0}
    tied = [
        {"basename": f"allow-{index}.jpg", "image_hash": row["image_hash"], "image_source": "original", "score": 40.0}
        for index, row in enumerate(allows)
    ]
    runtime = ShadowRuntime.load(tmp_path, enabled_config(), verify_source_hashes=False, enable_history=False)
    random.seed(911)
    selection_state = random.getstate()
    expected_rng = random.Random()
    expected_rng.setstate(selection_state)
    expected = expected_rng.choice(tied)["basename"]
    event = runtime.evaluate(
        quote_hash=veto["quote_id"], selected=selected, candidates=[selected, *tied], tie_break_state=selection_state,
    )
    assert event["alternative_image_basename"] == expected
    assert random.getstate() == selection_state


def test_legacy_incomplete_manifest_cannot_claim_global_no_safe_image(tmp_path: Path) -> None:
    manifest = load_manifest()
    quote_id = manifest["quotes_without_allowed_candidate_ids"][0]
    veto = next(row for row in manifest["pairs"].values() if row["quote_id"] == quote_id)
    selected = {"basename": "unsafe.jpg", "image_hash": veto["image_hash"], "image_source": "original", "score": 12.0}
    runtime = ShadowRuntime.load(tmp_path, enabled_config(), verify_source_hashes=False, enable_history=False)
    event = runtime.evaluate(quote_hash=quote_id, selected=selected, candidates=[selected])
    assert event["veto_category"] is None
    assert event["alternative_available"] is False
    assert event["alternative_reason"] == "quote_pair_coverage_incomplete"
    assert event["quote_has_no_allowed_candidate_globally"] is False
    assert event["quote_has_incomplete_pair_coverage"] is True


def test_complete_all_veto_runtime_coverage_reports_global_no_safe_image(tmp_path: Path) -> None:
    quote_id = "a" * 64
    image_hash = "b" * 64
    pair = {
        "quote_id": quote_id,
        "image_hash": image_hash,
        "decision": "veto",
        "veto_reason_codes": ["wrong_relationship"],
    }
    runtime = ShadowRuntime(
        True,
        tmp_path / "synthetic.json",
        pairs={f"{quote_id}:{image_hash}": pair},
        quote_flags={quote_id: False},
        quote_coverage={
            quote_id: {
                "allow_count": 0,
                "veto_count": 91,
                "adjudicated_unknown_count": 0,
                "not_adjudicated_count": 0,
                "complete_pair_coverage": True,
                "fully_resolved_pair_coverage": True,
                "global_no_safe_image": True,
            }
        },
    )
    selected = {
        "basename": "unsafe.jpg",
        "image_hash": image_hash,
        "image_source": "original",
        "score": 12.0,
    }

    event = runtime.evaluate(quote_hash=quote_id, selected=selected, candidates=[selected])

    assert event["veto_category"] == "coverage_gap_no_safe_image"
    assert event["alternative_reason"] == "quote_has_no_allowed_candidate_globally"
    assert event["quote_has_no_allowed_candidate_globally"] is True
    assert event["quote_has_incomplete_pair_coverage"] is False


def test_v3_gorbachev_coverage_distinguishes_unknown_from_missing() -> None:
    path = PROJECT / (
        "semantic_alignment_research/quote_attribution_cleanup_001/"
        "deployment_candidate/material_veto_v3_shadow_manifest.json"
    )
    runtime = ShadowRuntime.load(
        PROJECT,
        enabled_config(path),
        verify_source_hashes=False,
        enable_history=False,
    )
    quote_id = "67eacce6d9e102d4cf8a316451f9b8b9c095fdc6d0cffffb5a2d445e43b3d44d"
    gorbachev_image_hash = "f271019f2226396d8fdbc5297b968240d9654591b94f65778d7a84d2bc16a63a"
    unknown_key = next(
        key for key in runtime.adjudicated_unknown_pairs or {}
        if key.startswith(f"{quote_id}:")
    )

    assert runtime.quote_flags[quote_id] is None
    assert runtime.quote_coverage[quote_id]["adjudicated_unknown_count"] == 27
    assert runtime.quote_coverage[quote_id]["not_adjudicated_count"] == 54
    assert runtime.pair_adjudication(*unknown_key.split(":"))[0] == "adjudicated_unknown"
    assert runtime.pair_adjudication(
        quote_id, gorbachev_image_hash
    )[0] == "not_adjudicated_missing"


@pytest.mark.parametrize("mode", ["active", "enforce", "production", "replace", "filter", "prefer"])
def test_only_disabled_and_shadow_modes_are_accepted(mode: str) -> None:
    config = enabled_config()
    config["mode"] = mode
    assert any("disabled or shadow" in error for error in validate_shadow_config(config))


def test_missing_corrupt_and_stale_manifests_fail_open(tmp_path: Path) -> None:
    missing = ShadowRuntime.load(tmp_path, enabled_config(tmp_path / "missing.json"), enable_history=False)
    assert not missing.available and missing.status == "manifest_unavailable"
    corrupt_path = tmp_path / "corrupt.json"
    corrupt_path.write_text("{", encoding="utf-8")
    corrupt = ShadowRuntime.load(tmp_path, enabled_config(corrupt_path), enable_history=False)
    assert not corrupt.available
    stale_value = load_manifest()
    stale_value["source_file_hashes"] = {"x": {"path": "absent.json", "sha256": "0" * 64}}
    stale_path = tmp_path / "stale.json"
    stale_path.write_text(json.dumps(stale_value), encoding="utf-8")
    stale = ShadowRuntime.load(tmp_path, enabled_config(stale_path), enable_history=False)
    assert not stale.available and stale.status == "manifest_stale"


def test_preflight_and_replay_do_not_create_runtime_history(tmp_path: Path) -> None:
    result = shadow_preflight(PROJECT, MANIFEST)
    assert result["valid"] is True and result["network_calls"] == 0
    runtime_paths = (
        PROJECT / "bot_state.json",
        PROJECT / "lines_used.json",
        PROJECT / "images_used.json",
    )
    before = {
        path: sha256_file(path) if path.is_file() else None
        for path in runtime_paths
    }
    replay = historical_replay(PROJECT, MANIFEST, since_days=30)
    after = {
        path: sha256_file(path) if path.is_file() else None
        for path in runtime_paths
    }
    assert before == after
    assert replay["network_calls"] == 0
    assert replay["production_selection_change_failures"] == 0
    assert not (tmp_path / "quote_image_semantic_veto_runtime").exists()


def test_runtime_summary_does_not_mix_manifest_versions() -> None:
    old = {
        "shadow_status": "veto",
        "manifest_policy_version": "old-policy",
        "manifest_sha256": "a" * 64,
        "alternative_available": True,
        "production_selection_changed": False,
    }
    current = {
        "shadow_status": "allow",
        "manifest_policy_version": "current-policy",
        "manifest_sha256": "b" * 64,
        "production_selection_changed": False,
    }

    summary = summarise_events(
        [old, current],
        current_manifest_sha256="b" * 64,
        current_policy_version="current-policy",
    )

    assert summary["events"] == 1
    assert summary["allowed"] == 1
    assert summary["vetoed"] == 0
    assert summary["history_events_all_manifests"] == 2
    assert summary["events_excluded_from_current_manifest_summary"] == 1
    assert summary["mixed_manifest_versions"] is True
    assert len(summary["manifest_strata"]) == 2


def test_shadow_status_progress_uses_only_configured_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(MANIFEST.read_bytes())
    manifest_value = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_hash = sha256_file(manifest)
    (tmp_path / "mrsMThatcher.local.json").write_text(
        json.dumps({"quote_image_semantic_veto": enabled_config(manifest)}),
        encoding="utf-8",
    )
    runtime = tmp_path / "quote_image_semantic_veto_runtime"
    runtime.mkdir()
    rows = [
        {
            "shadow_status": "veto",
            "manifest_policy_version": "retired-policy",
            "manifest_sha256": "f" * 64,
            "production_selection_changed": False,
        },
        {
            "shadow_status": "allow",
            "manifest_policy_version": manifest_value["policy_version"],
            "manifest_sha256": manifest_hash,
            "production_selection_changed": False,
        },
    ]
    (runtime / "shadow_history.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    monkeypatch.setattr(semantic_veto, "manifest_source_hash_mismatches", lambda *_args: [])

    status = shadow_status(tmp_path)

    assert status["events"] == 1
    assert status["allowed"] == 1
    assert status["vetoed"] == 0
    assert status["observation_progress"] == {"toward_100": 1, "toward_200": 1}
    assert status["events_excluded_from_current_manifest_summary"] == 1


def test_shadow_status_rejects_manifest_with_missing_recorded_sources(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(MANIFEST.read_bytes())
    (tmp_path / "mrsMThatcher.local.json").write_text(
        json.dumps({"quote_image_semantic_veto": enabled_config(manifest)}),
        encoding="utf-8",
    )

    status = shadow_status(tmp_path)

    assert status["manifest"]["valid"] is False
    assert "source hash mismatch" in status["manifest"]["reason"]
    assert status["manifest"]["sha256"] == sha256_file(manifest)

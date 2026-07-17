from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft7Validator
from PIL import Image

import analyse_mrs_assets_xai_v4 as canonical
from semantic_alignment import discovered_image_preparation as prep
from semantic_alignment.io import atomic_write_json, read_json, sha256_file


ROOT = Path(__file__).resolve().parents[1]


def image(path: Path, size=(800, 600), colour=(30, 70, 110)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path, format="JPEG")


def baseline_analysis() -> dict:
    baseline = read_json(ROOT / "image_analysis.json")
    return next(iter(baseline["items"].values()))["analysis"]


def make_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    research = project / "image_discovery_research/run"
    work = research / "integration_preparation"
    original = project / "images/t70.jpg"
    image(original)
    digest = sha256_file(original)
    analysis = baseline_analysis()
    atomic_write_json(project / "image_analysis.json", {
        "schema_version": 3, "model": "grok-4.3", "prompt_version": canonical.IMAGE_PROMPT_VERSION,
        "items": {digest: {"analysis": analysis, "usage": {"cost_in_usd_ticks": 50_000_000}}},
        "path_index": {"t70.jpg": digest}, "current_hashes": [digest],
    })
    atomic_write_json(project / "generated_image_analysis.json", {
        "schema_version": 3, "items": {}, "path_index": {}, "current_hashes": [],
    })
    (project / "mrsMThatcher2.py").write_text("# fixture\n", encoding="utf-8")
    candidate = research / "exported_kept/production_ready/candidate-a.jpg"
    image(candidate, colour=(120, 40, 30))
    candidate_hash = sha256_file(candidate)
    work.mkdir(parents=True)
    record = {
        "candidate_id": "a", "export_group": "production_ready",
        "export_relative_path": "production_ready/candidate-a.jpg",
        "original_filename": "a.jpg", "original_sha256": candidate_hash,
        "rights_status": "public_domain", "identity_basis": "archive_record",
        "identity_evidence": "Archive caption names Margaret Thatcher", "identity_confidence": "high",
        "publisher": "Archive", "caption": "Caption", "required_attribution_wording": "",
    }
    atomic_write_json(work / "production_ready_manifest.json", {"records": [record]})
    atomic_write_json(work / "rights_pending_manifest.json", {"records": []})
    atomic_write_json(work / "maybe_manifest.json", {"records": []})
    atomic_write_json(work / "human_pairing_review.json", {"schema_version": 1, "reviews": {}, "updated_at": None})
    atomic_write_json(work / "maybe_second_pass_reviews.json", {"schema_version": 1, "reviews": {}, "updated_at": None})
    return project, research, work


def test_production_hashes_are_deterministic_and_ignore_appledouble(tmp_path):
    project, _research, _work = make_project(tmp_path)
    (project / "images/._t70.jpg").write_bytes(b"noise")
    first = prep.production_hashes(project)
    second = prep.production_hashes(project)
    assert first == second
    assert all("._t70" not in row["path"] for row in first["records"])


def test_filename_plan_continues_after_highest_number_without_using_gap(tmp_path):
    project, _research, work = make_project(tmp_path)
    (project / "images/t53.jpg").write_bytes(b"occupied gap")
    value = prep.filename_plan(project, work)
    assert value["records"][0]["proposed_name"] == "t71.jpg"
    assert value["records"][0]["applied"] is False


def test_filename_plan_preserves_existing_assignments_and_appends_promoted_maybe(tmp_path):
    project, research, work = make_project(tmp_path)
    maybe_image = research / "exported_kept/maybe/candidate-m.jpg"
    image(maybe_image)
    atomic_write_json(work / "maybe_manifest.json", {"records": [{
        "candidate_id": "m", "export_relative_path": "maybe/candidate-m.jpg",
        "original_filename": "m.jpg", "original_sha256": sha256_file(maybe_image),
        "rights_status": "public_domain",
    }]})
    atomic_write_json(work / "maybe_second_pass_reviews.json", {"reviews": {
        "m": {"candidate_id": "m", "decision": "promote_keep"},
    }})
    atomic_write_json(work / "filename_plan.json", {"records": [{
        "candidate_id": "a", "source_export_path": "production_ready/candidate-a.jpg",
        "old_name": "a.jpg", "proposed_name": "t71.jpg", "sha256": "old", "applied": False,
        "reverse_operation": {"from": "t71.jpg", "to": "a.jpg"},
    }]})

    value = prep.filename_plan(project, work)
    by_id = {row["candidate_id"]: row for row in value["records"]}

    assert by_id["a"]["proposed_name"] == "t71.jpg"
    assert by_id["m"]["proposed_name"] == "t72.jpg"
    assert len(value["records"]) == 2


def test_filename_plan_bounds_use_numeric_names_not_candidate_order():
    plan = {"records": [
        {"candidate_id": "a", "proposed_name": "t71.jpg"},
        {"candidate_id": "b", "proposed_name": "t94.jpg"},
        {"candidate_id": "z", "proposed_name": "t92.jpg"},
    ]}

    assert prep.planned_filename_bounds(plan) == ["t71.jpg", "t94.jpg"]


def test_duplicate_audit_detects_exact_image_without_discarding(tmp_path):
    project, research, work = make_project(tmp_path)
    shutil.copyfile(project / "images/t70.jpg", research / "exported_kept/production_ready/candidate-a.jpg")
    record = read_json(work / "production_ready_manifest.json")["records"][0]
    record["original_sha256"] = sha256_file(research / "exported_kept/production_ready/candidate-a.jpg")
    atomic_write_json(work / "production_ready_manifest.json", {"records": [record]})
    value = prep.duplicate_audit(project, research, work)
    assert value["records"][0]["classification"] == "manual_review_required"
    assert value["records"][0]["matches"][0]["similarity"] == "exact_duplicate"


def test_duplicate_audit_marks_higher_resolution_related_image_as_replacement(tmp_path):
    project, research, work = make_project(tmp_path)
    image(project / "images/t70.jpg", size=(400, 300), colour=(50, 80, 120))
    image(research / "exported_kept/production_ready/candidate-a.jpg", size=(800, 600), colour=(50, 80, 120))
    baseline = read_json(project / "image_analysis.json")
    old_hash = next(iter(baseline["items"]))
    new_hash = sha256_file(project / "images/t70.jpg")
    baseline["items"][new_hash] = baseline["items"].pop(old_hash)
    baseline["path_index"]["t70.jpg"] = new_hash
    baseline["current_hashes"] = [new_hash]
    atomic_write_json(project / "image_analysis.json", baseline)
    record = read_json(work / "production_ready_manifest.json")["records"][0]
    record["original_sha256"] = sha256_file(research / "exported_kept/production_ready/candidate-a.jpg")
    atomic_write_json(work / "production_ready_manifest.json", {"records": [record]})
    value = prep.duplicate_audit(project, research, work)
    assert value["records"][0]["classification"] == "higher_quality_replacement"


def test_analysis_preflight_uses_canonical_schema_and_bounded_cost(tmp_path):
    project, _research, work = make_project(tmp_path)
    value = prep.analysis_preflight(project, work)
    assert value["candidate_count"] == 1
    assert value["image_schema_version"] == 3
    assert value["identity_instruction_verified"] is True
    assert value["hard_cost_ceiling_usd"] == 1.0
    assert value["network_calls_made"] is False


def test_source_grounded_metadata_extracts_named_people_only_from_source(tmp_path):
    _project, research, work = make_project(tmp_path)
    atomic_write_json(research / "candidate_manifest.json", {"records": [{
        "candidate_id": "a",
        "page_title": "President Ronald Reagan and Prime Minister Margaret Thatcher.jpg",
        "caption": "Ronald Reagan meets Margaret Thatcher at Camp David.",
        "alt_text": "",
        "identity_evidence": "Archive catalogue names Ronald Reagan and Margaret Thatcher.",
        "identity_basis": "archive_record",
        "identity_confidence": "high",
        "approximate_date": "1984-12-22",
        "event_or_period": "Working meeting at Camp David",
        "source_page_url": "https://example.test/archive/a",
    }]})

    value = prep.build_source_grounded_metadata(research, work)
    record = value["records"][0]

    assert record["named_people"] == ["Margaret Thatcher", "Ronald Reagan"]
    assert "Camp David" in record["source_evidence_text"]
    assert record["identity_basis"] == "archive_record"
    assert record["source_text_sha256"]


def test_source_grounded_metadata_does_not_import_visual_model_identity(tmp_path):
    _project, research, work = make_project(tmp_path)
    atomic_write_json(research / "candidate_manifest.json", {"records": [{
        "candidate_id": "a",
        "page_title": "Margaret Thatcher portrait.jpg",
        "caption": "Margaret Thatcher.",
        "identity_evidence": "Archive caption names Margaret Thatcher.",
        "identity_basis": "archive_record",
        "identity_confidence": "high",
        "source_page_url": "https://example.test/archive/a",
        "visual_analysis": {"description": "Margaret Thatcher with Ronald Reagan"},
    }]})

    value = prep.build_source_grounded_metadata(research, work)

    assert value["records"][0]["named_people"] == ["Margaret Thatcher"]


def test_source_event_summary_compacts_archive_catalogue_text():
    g7 = prep._source_event_summary({
        "caption": "G-7 Economic Summit leaders at the University of Toronto in Canada (left to right) A, B, C.",
    })
    dutch = prep._source_event_summary({
        "caption": "Collectie: Anefo Beschrijving : Aankomst van premier Thatcher op vliegveld Valkenburg met minister Van Aardenne Datum : 19 september 1983 Locatie : Valkenburg",
    })

    assert g7 == "G-7 Economic Summit leaders at the University of Toronto in Canada"
    assert dutch == "Aankomst van premier Thatcher op vliegveld Valkenburg met minister Van Aardenne"


def test_source_grounded_prompt_uses_archive_names_without_face_inference():
    context = {
        "candidate_id": "a",
        "named_people": ["Margaret Thatcher", "Ronald Reagan"],
        "source_evidence_text": "Ronald Reagan meets Margaret Thatcher at Camp David.",
        "identity_basis": "archive_record",
        "identity_confidence": "high",
        "approximate_date": "1984-12-22",
        "event_or_period": "Working meeting at Camp David",
        "source_page_url": "https://example.test/archive/a",
    }

    prompt = prep.source_grounded_user_prompt(context)

    assert "Margaret Thatcher" in prompt and "Ronald Reagan" in prompt
    assert "Do not identify any additional person from facial appearance" in prompt
    assert "archive_record" in prompt


def test_source_identity_overlay_guarantees_names_and_specific_context():
    analysis = baseline_analysis()
    context = {
        "named_people": ["Margaret Thatcher", "Ronald Reagan"],
        "identity_confidence": "high",
        "event_or_period": "Working luncheon at Camp David",
        "approximate_date": "22 December 1984",
    }

    corrected = prep.apply_source_identity_overlay(analysis, context)

    hint = corrected["historical_context"]["event_or_context_hint"]
    assert "Margaret Thatcher" in hint and "Ronald Reagan" in hint
    assert corrected["historical_context"]["specificity"] == "specific_event"
    assert corrected["historical_context"]["confidence"] >= 90
    list(Draft7Validator(canonical.IMAGE_ANALYSIS_SCHEMA).iter_errors(corrected)) == []


def test_source_identity_overlay_caps_long_event_hint_to_schema_limit():
    analysis = baseline_analysis()
    context = {
        "named_people": [
            "Margaret Thatcher", "Ronald Reagan", "Brian Mulroney", "Helmut Kohl",
            "François Mitterrand", "Jacques Delors", "Noboru Takeshita",
        ],
        "identity_confidence": "high",
        "source_event_summary": "G7 summit " + ("with extensive source detail " * 30),
        "approximate_date": "20 June 1988",
    }

    corrected = prep.apply_source_identity_overlay(analysis, context)

    assert len(corrected["historical_context"]["event_or_context_hint"]) <= 260
    assert not list(Draft7Validator(canonical.IMAGE_ANALYSIS_SCHEMA).iter_errors(corrected))


def test_source_grounded_normalisation_truncates_only_bounded_prose():
    analysis = baseline_analysis()
    analysis["description"] = "Margaret Thatcher and Mikhail Gorbachev. " + ("Detailed source-grounded scene. " * 30)
    analysis["scene_summary"] = "Margaret Thatcher with Mikhail Gorbachev. " + ("Context. " * 50)

    normalised = prep.normalise_source_grounded_analysis(analysis)

    assert len(normalised["description"]) <= 520
    assert len(normalised["scene_summary"]) <= 360
    assert "Margaret Thatcher" in normalised["description"]
    assert "Mikhail Gorbachev" in normalised["description"]
    assert not list(Draft7Validator(canonical.IMAGE_ANALYSIS_SCHEMA).iter_errors(normalised))


def test_analysis_requires_exact_confirmation_and_caches_by_hash(tmp_path, monkeypatch):
    project, research, work = make_project(tmp_path)
    monkeypatch.setenv("XAI_API_KEY", "not-a-real-key")
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        return canonical.ApiResult(
            content=baseline_analysis(), usage={"cost_in_usd_ticks": 50_000_000}, response_id="response",
        )

    monkeypatch.setattr(canonical, "call_xai_structured", fake_call)
    with pytest.raises(RuntimeError, match="exact"):
        prep.analyse_candidates(project, research, work, execute=True, confirmed_cost=0.99)
    first = prep.analyse_candidates(project, research, work, execute=True, confirmed_cost=1.0)
    second = prep.analyse_candidates(project, research, work, execute=True, confirmed_cost=1.0)
    assert first["completed_total"] == 1 and second["completed_now"] == 0
    assert len(calls) == 1
    assert "Do not identify people by name" in calls[0]["messages"][0]["content"]
    output = read_json(work / "canonical_image_analysis.json")
    assert output["schema_version"] == 3
    assert next(iter(output["items"].values()))["identity_provenance"]["model_was_not_asked_to_identify_person"] is True


def test_source_grounded_analysis_is_separate_cached_and_names_source_figures(tmp_path, monkeypatch):
    project, research, work = make_project(tmp_path)
    atomic_write_json(research / "candidate_manifest.json", {"records": [{
        "candidate_id": "a",
        "page_title": "President Ronald Reagan and Prime Minister Margaret Thatcher at Camp David.jpg",
        "caption": "Ronald Reagan meets Margaret Thatcher at Camp David.",
        "identity_evidence": "Archive catalogue names Ronald Reagan and Margaret Thatcher.",
        "identity_basis": "archive_record", "identity_confidence": "high",
        "approximate_date": "1984-12-22", "source_page_url": "https://example.test/a",
    }]})
    prep.build_source_grounded_metadata(research, work)
    monkeypatch.setenv("XAI_API_KEY", "not-a-real-key")
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        return canonical.ApiResult(
            content=baseline_analysis(), usage={"cost_in_usd_ticks": 50_000_000}, response_id="grounded-response",
        )

    monkeypatch.setattr(canonical, "call_xai_structured", fake_call)
    first = prep.analyse_source_grounded_candidates(
        project, research, work, execute=True, confirmed_cost=1.0,
    )
    second = prep.analyse_source_grounded_candidates(
        project, research, work, execute=True, confirmed_cost=1.0,
    )

    assert first["completed_now"] == 1 and second["completed_now"] == 0
    assert len(calls) == 1
    assert "Names explicitly supplied" in calls[0]["messages"][0]["content"]
    output = read_json(work / "source_grounded_image_analysis.json")
    item = next(iter(output["items"].values()))
    assert item["source_grounded_context"]["named_people"] == ["Margaret Thatcher", "Ronald Reagan"]
    assert "Ronald Reagan" in item["analysis"]["historical_context"]["event_or_context_hint"]
    assert not (work / "canonical_image_analysis.json").exists()


def test_source_grounded_schema_failure_persists_response_and_cost(tmp_path, monkeypatch):
    project, research, work = make_project(tmp_path)
    atomic_write_json(research / "candidate_manifest.json", {"records": [{
        "candidate_id": "a", "page_title": "Margaret Thatcher portrait.jpg",
        "caption": "Margaret Thatcher.", "identity_evidence": "Archive names Margaret Thatcher.",
        "identity_basis": "archive_record", "identity_confidence": "high",
        "source_page_url": "https://example.test/a",
    }]})
    prep.build_source_grounded_metadata(research, work)
    monkeypatch.setenv("XAI_API_KEY", "not-a-real-key")

    def fake_call(**_kwargs):
        return canonical.ApiResult(
            content=baseline_analysis(), usage={"cost_in_usd_ticks": 50_000_000}, response_id="failed-response",
        )

    def invalid_overlay(analysis, _context):
        value = json.loads(json.dumps(analysis))
        value["historical_context"]["event_or_context_hint"] = "x" * 261
        return value

    monkeypatch.setattr(canonical, "call_xai_structured", fake_call)
    monkeypatch.setattr(prep, "apply_source_identity_overlay", invalid_overlay)

    result = prep.analyse_source_grounded_candidates(
        project, research, work, execute=True, confirmed_cost=1.0,
    )

    assert result["failures"] == 1
    ledger = read_json(work / "source_grounded_analysis_cost_ledger.json")
    assert ledger["source_grounded_cost_in_usd_ticks"] == 50_000_000
    billed = read_json(work / "source_grounded_billed_responses.json")
    assert billed["responses"]["failed-response"]["cost_in_usd_ticks"] == 50_000_000
    raw = work / "source_grounded_raw_responses/failed-response.json"
    assert read_json(raw)["content"] == baseline_analysis()

def test_source_grounded_raw_response_can_be_recovered_without_network(tmp_path, monkeypatch):
    project, research, work = make_project(tmp_path)
    atomic_write_json(research / "candidate_manifest.json", {"records": [{
        "candidate_id": "a", "page_title": "Margaret Thatcher portrait.jpg",
        "caption": "Margaret Thatcher.", "identity_evidence": "Archive names Margaret Thatcher.",
        "identity_basis": "archive_record", "identity_confidence": "high",
        "source_page_url": "https://example.test/a",
    }]})
    metadata = prep.build_source_grounded_metadata(research, work)["records"][0]
    model = read_json(project / "image_analysis.json")["model"]
    db = prep._fresh_analysis_db(project, work, model)
    db["prompt_version"] = prep.SOURCE_GROUNDED_PROMPT_VERSION
    db["analysis_kind"] = "source_grounded_images"
    digest = metadata["image_sha256"]
    db["failures"][digest] = {"candidate_id": "a", "error": "schema validation failed"}
    atomic_write_json(work / "source_grounded_image_analysis.json", db)
    atomic_write_json(work / "source_grounded_analysis_cost_ledger.json", {
        "completed_count": 0, "failure_count": 1,
    })
    raw_analysis = baseline_analysis()
    raw_analysis["description"] = "Margaret Thatcher. " + ("Detailed portrait. " * 40)
    atomic_write_json(work / "source_grounded_raw_responses/response-a.json", {
        "candidate_id": "a", "image_hash": digest, "source_text_sha256": metadata["source_text_sha256"],
        "response_id": "response-a", "usage": {"cost_in_usd_ticks": 50_000_000},
        "content": raw_analysis, "received_at": "2026-01-01T00:00:00Z",
    })
    monkeypatch.setattr(canonical, "call_xai_structured", lambda **_kwargs: pytest.fail("network call not expected"))

    recovered = prep.recover_source_grounded_raw(project, research, work)

    assert recovered["recovered_count"] == 1
    output = read_json(work / "source_grounded_image_analysis.json")
    assert not output["failures"]
    assert "Margaret Thatcher" in output["items"][digest]["analysis"]["description"]
    ledger = read_json(work / "source_grounded_analysis_cost_ledger.json")
    assert ledger["completed_count"] == 1
    assert ledger["failure_count"] == 0


def test_refresh_source_overlays_updates_compiled_event_without_network(tmp_path, monkeypatch):
    project, research, work = make_project(tmp_path)
    atomic_write_json(research / "candidate_manifest.json", {"records": [{
        "candidate_id": "a", "page_title": "Margaret Thatcher portrait.jpg",
        "caption": "Margaret Thatcher at a documented event (left to right) unrelated catalogue list",
        "identity_evidence": "Archive names Margaret Thatcher.",
        "identity_basis": "archive_record", "identity_confidence": "high",
        "source_page_url": "https://example.test/a",
    }]})
    context = prep.build_source_grounded_metadata(research, work)["records"][0]
    digest = context["image_sha256"]
    analysis = prep.apply_source_identity_overlay(baseline_analysis(), {
        **context, "source_event_summary": "old verbose event summary",
    })
    atomic_write_json(work / "source_grounded_image_analysis.json", {
        "schema_version": 3, "model": "grok-4.3", "prompt_version": prep.SOURCE_GROUNDED_PROMPT_VERSION,
        "items": {digest: {
            "analysis": analysis, "source_grounded_context": {**context, "source_event_summary": "old verbose event summary"},
            "analysis_model": "grok-4.3", "prompt_version": prep.SOURCE_GROUNDED_PROMPT_VERSION,
            "source_text_sha256": context["source_text_sha256"], "paths": ["production_ready/candidate-a.jpg"],
        }}, "failures": {}, "path_index": {}, "current_hashes": [],
    })
    monkeypatch.setattr(canonical, "call_xai_structured", lambda **_kwargs: pytest.fail("network call not expected"))

    result = prep.refresh_source_grounded_overlays(research, work)

    assert result["updated_count"] == 1
    updated = read_json(work / "source_grounded_image_analysis.json")["items"][digest]
    assert "unrelated catalogue list" not in updated["analysis"]["historical_context"]["event_or_context_hint"]


def test_source_grounded_metadata_audit_requires_all_source_names(tmp_path):
    _project, research, work = make_project(tmp_path)
    atomic_write_json(research / "candidate_manifest.json", {"records": [{
        "candidate_id": "a", "page_title": "Ronald Reagan and Margaret Thatcher.jpg",
        "caption": "Ronald Reagan meets Margaret Thatcher.",
        "identity_evidence": "Archive names Ronald Reagan and Margaret Thatcher.",
        "identity_basis": "archive_record", "identity_confidence": "high",
        "source_page_url": "https://example.test/a",
    }]})
    context = prep.build_source_grounded_metadata(research, work)["records"][0]
    analysis = prep.apply_source_identity_overlay(baseline_analysis(), context)
    atomic_write_json(work / "source_grounded_image_analysis.json", {
        "schema_version": 3, "model": "grok-4.3", "prompt_version": prep.SOURCE_GROUNDED_PROMPT_VERSION,
        "items": {context["image_sha256"]: {"analysis": analysis, "source_grounded_context": context}},
        "failures": {},
    })

    result = prep.audit_source_grounded_metadata(research, work)

    assert result["passed"] is True
    assert result["named_person_reference_count"] == 2
    assert result["named_person_counts"] == {"Margaret Thatcher": 1, "Ronald Reagan": 1}
    assert result["missing_identity_bindings"] == []
    status = read_json(work / "metadata_review_status.json")
    assert status["automated_source_grounded_validation"] == "passed"
    assert status["human_metadata_validation"] == "not_performed"


def test_source_grounded_matching_refuses_incomplete_corrected_analysis(tmp_path):
    project, research, work = make_project(tmp_path)
    atomic_write_json(research / "candidate_manifest.json", {"records": [{
        "candidate_id": "a", "page_title": "Margaret Thatcher portrait.jpg",
        "caption": "Margaret Thatcher.", "identity_evidence": "Archive names Margaret Thatcher.",
        "identity_basis": "archive_record", "identity_confidence": "high",
        "source_page_url": "https://example.test/a",
    }]})
    atomic_write_json(work / "source_grounded_image_analysis.json", {
        "schema_version": 3, "items": {}, "failures": {"x": {"error": "failed"}},
    })

    with pytest.raises(RuntimeError, match="source-grounded metadata audit"):
        prep.quote_matching_dry_run(project, work)


def test_quote_matching_uses_production_scorer_without_production_writes(tmp_path):
    project, _research, work = make_project(tmp_path)
    candidate = read_json(work / "production_ready_manifest.json")["records"][0]
    analysis = baseline_analysis()
    atomic_write_json(work / "canonical_image_analysis.json", {
        "schema_version": 3,
        "items": {candidate["original_sha256"]: {"analysis": analysis}},
    })
    atomic_write_json(work / "filename_plan.json", {"records": [{
        "candidate_id": "a", "sha256": candidate["original_sha256"], "proposed_name": "t71.jpg",
    }]})
    quote_db = read_json(ROOT / "quote_analysis.json")
    quote_hash, quote_item = next(iter(quote_db["items"].items()))
    atomic_write_json(project / "quote_analysis.json", {"items": {quote_hash: quote_item}})
    before = prep.production_hashes(project)
    value = prep.quote_matching_dry_run(project, work)
    after = prep.production_hashes(project)
    assert value["state_mutated"] is False
    assert value["quote_count"] == 1 and value["candidate_count"] == 1
    assert before == after


def test_pairing_review_is_idempotent_and_audited(tmp_path):
    _project, _research, work = make_project(tmp_path)
    atomic_write_json(work / "quote_matching_dry_run.json", {
        "records": [{
            "candidate_id": "a", "top_winning_matches": [{
                "quote_hash": "q", "quote_text": "Quote", "score": 10.0, "components": {},
                "nearest_competing_existing_image": "t70.jpg", "nearest_competing_score": 9.0,
            }], "top_proposed_matches": [],
        }],
    })
    first = prep.save_pairing_review(work, "a:q", "approve_pairing", "good")
    second = prep.save_pairing_review(work, "a:q", "approve_pairing", "good")
    assert first == second
    assert len((work / "human_pairing_review_audit.jsonl").read_text().splitlines()) == 1


def test_pairing_review_state_counts_only_current_pairs(tmp_path):
    project, research, work = make_project(tmp_path)
    candidate = read_json(work / "production_ready_manifest.json")["records"][0]
    atomic_write_json(work / "canonical_image_analysis.json", {
        "schema_version": 3, "items": {candidate["original_sha256"]: {"analysis": baseline_analysis()}},
    })
    atomic_write_json(work / "filename_plan.json", {"records": [{
        "candidate_id": "a", "sha256": candidate["original_sha256"], "proposed_name": "t71.jpg",
    }]})
    atomic_write_json(work / "quote_matching_dry_run.json", {"records": []})
    atomic_write_json(work / "human_pairing_review.json", {"reviews": {
        "old:pair": {"decision": "approve_pairing"},
    }})

    state = prep.pairing_review_state(research, work)

    assert state["reviewed_count"] == 0
    assert state["unreviewed_count"] == 0


def test_review_results_do_not_treat_missing_metadata_flags_as_approval(tmp_path):
    _project, _research, work = make_project(tmp_path)
    atomic_write_json(work / "quote_matching_dry_run.json", {
        "records": [{
            "candidate_id": "a", "top_winning_matches": [{
                "quote_hash": "q1", "quote_text": "First", "score": 10.0, "components": {},
                "nearest_competing_existing_image": "t70.jpg", "nearest_competing_score": 9.0,
            }, {
                "quote_hash": "q2", "quote_text": "Second", "score": 8.0, "components": {},
                "nearest_competing_existing_image": "t70.jpg", "nearest_competing_score": 9.0,
            }], "top_proposed_matches": [],
        }],
    })
    prep.save_pairing_review(work, "a:q1", "approve_pairing", "")
    prep.save_pairing_review(work, "a:q2", "prefer_existing_image", "poor fit")

    result = prep.compile_review_results(work)

    assert result["pairing_review_complete"] is True
    assert result["decision_counts"] == {"approve_pairing": 1, "prefer_existing_image": 1}
    assert result["approved_pairing_count"] == 1
    assert result["metadata_validation"]["status"] == "not_performed"
    assert result["metadata_validation"]["absence_of_correction_flags_is_approval"] is False
    assert result["metadata_validation"]["candidate_statuses"] == {"a": "not_human_validated"}


def test_review_results_preserve_source_grounded_metadata_audit(tmp_path):
    _project, _research, work = make_project(tmp_path)
    atomic_write_json(work / "quote_matching_dry_run.json", {"records": []})
    source_status = {
        "schema_version": 2,
        "automated_source_grounded_validation": "passed",
        "human_metadata_validation": "not_performed",
        "safe_for_offline_selector_dry_run": True,
    }
    atomic_write_json(work / "metadata_review_status.json", source_status)

    result = prep.compile_review_results(work)

    assert result["metadata_validation"] == source_status
    assert read_json(work / "metadata_review_status.json") == source_status


def test_review_source_records_include_promoted_maybe(tmp_path):
    _project, research, work = make_project(tmp_path)
    maybe_image = research / "exported_kept/maybe/candidate-m.jpg"
    image(maybe_image)
    atomic_write_json(work / "maybe_manifest.json", {"records": [{
        "candidate_id": "m", "export_relative_path": "maybe/candidate-m.jpg",
        "original_filename": "m.jpg", "original_sha256": sha256_file(maybe_image),
        "rights_status": "public_domain",
    }]})
    atomic_write_json(work / "maybe_second_pass_reviews.json", {"reviews": {
        "m": {"candidate_id": "m", "decision": "promote_keep"},
    }})

    source = prep.review_source_records(work)

    assert set(source) == {"a", "m"}


def test_review_results_reconcile_maybe_promotions(tmp_path):
    _project, _research, work = make_project(tmp_path)
    maybe_path = work / "maybe_manifest.json"
    atomic_write_json(maybe_path, {"records": [
        {"candidate_id": "m1"}, {"candidate_id": "m2"},
    ]})
    atomic_write_json(work / "maybe_second_pass_reviews.json", {
        "schema_version": 1,
        "reviews": {
            "m1": {"candidate_id": "m1", "decision": "promote_keep"},
            "m2": {"candidate_id": "m2", "decision": "remain_maybe"},
        },
    })
    atomic_write_json(work / "quote_matching_dry_run.json", {"records": []})

    result = prep.compile_review_results(work)

    assert result["maybe_review_complete"] is True
    assert result["maybe_decision_counts"] == {"promote_keep": 1, "remain_maybe": 1}
    assert result["promoted_maybe_candidate_ids"] == ["m1"]


def test_reviewer_is_responsive_and_uses_local_images_only():
    assert "@media(max-width:800px)" in prep.PAIRING_HTML
    assert "direct_image_url" not in prep.PAIRING_HTML
    assert "http://" not in prep.PAIRING_HTML and "https://" not in prep.PAIRING_HTML
    assert "Metadata needs correction" in prep.PAIRING_HTML
    assert "Promote to keep" in prep.MAYBE_HTML
    assert "new.src" not in prep.PAIRING_HTML
    assert "el('newImage')" in prep.PAIRING_HTML
    assert "Next unreviewed" in prep.PAIRING_HTML
    assert "Show unreviewed only" in prep.PAIRING_HTML
    assert "nextUnreviewed" in prep.PAIRING_HTML
    assert "unreviewed_count" in prep.PAIRING_HTML


def test_production_hash_verification_detects_change(tmp_path):
    project, _research, work = make_project(tmp_path)
    atomic_write_json(work / "production_hashes_before.json", prep.production_hashes(project))
    assert prep.verify_production_unchanged(project, work)["unchanged"] is True
    (project / "images/t70.jpg").write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="changed"):
        prep.verify_production_unchanged(project, work)


def test_source_has_no_x_write_or_production_copy_operation():
    source = (ROOT / "semantic_alignment/discovered_image_preparation.py").read_text(encoding="utf-8")
    assert "api.twitter.com" not in source and "api.x.com" not in source
    assert "post_tweet" not in source and "create_tweet" not in source
    assert "shutil.copy2(source, project_dir / \"images\"" not in source


def test_status_cli_stdout_is_machine_readable_json(tmp_path):
    project = tmp_path / "project"
    research = project / "image_discovery_research/run"
    work = research / "integration_preparation"
    work.mkdir(parents=True)
    completed = subprocess.run(
        [
            sys.executable, str(ROOT / "prepare_discovered_images.py"), "status",
            "--project-dir", str(project), "--research-dir", str(research), "--work-dir", str(work),
        ],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    assert json.loads(completed.stdout)["work_dir"] == str(work)

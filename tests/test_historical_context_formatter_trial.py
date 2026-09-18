from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

import historical_context_formatter as v1
from semantic_alignment.historical_context_formatter_trial import (
    FORMATTER_V2,
    _forbidden_candidate_text,
    _near_duplicate,
    blind_presentations,
    build_blind_assignments,
    format_british_date,
    format_context_reply_v2,
    generate_review_results,
    meaning_decision,
    prepare_trial,
    read_json,
    save_review,
    select_review_sample,
    strict_audit,
)

from tests.helpers.historical_corpus import (
    RESEARCH_RELATIVE,
    historical_corpus_root,
)
V1_GOLDEN_OUTPUTS = {
    "00426881d2746c35657e8bb3103febb15cf13f2342bb65604a23a278b608064d": "12a7f59258368688c1c87e648c9868bc1b70f4ce6917a0df9f5e0ac518622fc9",
    "e28d24c49780a4d8a0c248097ee4962f941fdf1b2ec687a1bf52cddabc95995b": "2e0fe6193506d15aeaeda83a5024d6a19f5eec664686fb3b25733b9aca9325a1",
    "8c839e92d3961147ef0070f049a2caa7f0c070bffafe4988153659825d9fa50b": "123bc7e6455a36ef82d14ed2c47ba1e14cc515402bf91ef51b8f897568258a34",
}
REVIEWED_MEANING_REASON_CHANGES = {
    "cac5746ca684f9611a25dcfb6b024ed63bfb3d41b2fa4c5c3d6e44290378d4ea": {
        "before": (
            "Meaning retained because the argument is counter-intuitive or "
            "contrastive."
        ),
        "after": (
            "Meaning retained because the packet records a distinct "
            "explanatory mechanism."
        ),
    },
}


@pytest.fixture(scope="module")
def corpus(historical_corpus_root):
    # Frozen formatter-trial fixtures predate the source-role audit. Keep this
    # corpus audit-free so the byte-parity assertions continue to exercise V2.
    return v1.load_and_validate_corpus(
        historical_corpus_root / RESEARCH_RELATIVE, load_source_role_audit=False,
    )


@pytest.fixture(scope="module")
def prepared(tmp_path_factory, historical_corpus_root):
    output = tmp_path_factory.mktemp("formatter_trial") / "trial"
    prepare_trial(historical_corpus_root / RESEARCH_RELATIVE, output, 50)
    return output


def _packet(**updates):
    packet = {
        "quote_id": "a" * 64,
        "quote_text": "Freedom demands diffusion of power because concentrated power destroys liberty.",
        "verification_status": "exact",
        "source_event": "Speech to an audience",
        "date": "March 30, 1978",
        "immediate_subject": "The relationship between liberty and institutional power.",
        "historical_context": "A speech about liberty.",
        "intended_argument": "That concentrated power destroys liberty and freedom requires dispersed power.",
        "literal_meaning": "Power must be dispersed to preserve liberty.",
        "mechanism": "Concentrated authority removes individual choice.",
        "claimed_consequence": "Liberty is lost.",
        "text_variation_notes": "",
        "unresolved_questions": [],
        "stable_locator": "Margaret Thatcher Foundation Archive, Document 123456",
        "sources": [],
        "research_confidence": "high",
    }
    packet.update(updates)
    return packet


def test_exactly_627_completed_and_five_unresolved(corpus):
    packets, unresolved = corpus
    assert len(packets) == 627 and len(unresolved) == 5
    assert not set(packets) & unresolved


def test_production_v1_golden_outputs_are_byte_stable(corpus):
    packets, _ = corpus
    for quote_id, expected in V1_GOLDEN_OUTPUTS.items():
        text = v1.format_context_reply(packets[quote_id])["text"]
        assert hashlib.sha256(text.encode()).hexdigest() == expected


def test_promoted_v2_matches_frozen_candidate_or_reviewed_correction(
    corpus, historical_corpus_root,
):
    packets, _ = corpus
    research = historical_corpus_root / RESEARCH_RELATIVE
    raw_packets, _raw_unresolved = v1.load_and_validate_corpus_core(research)
    frozen = read_json(
        Path("semantic_alignment_research/historical_context_formatter_trial_001")
        / "formatter_v2_candidate.json"
    )["items"]
    corrections = read_json(
        research / "historical_context_packet_corrections.json"
    )["items"]
    transition = read_json(
        Path("historical_context_v8_v9_transition_manifest.json")
    )["items"]
    statecraft_transition = read_json(
        Path("historical_context_v9_statecraft_primary_transition_manifest.json")
    )["items"]
    mtf_corpus_transition = read_json(
        Path("historical_context_v9_mtf_corpus_evidence_transition_manifest.json")
    )["items"]
    mtf_live_transition = read_json(
        Path("historical_context_v9_mtf_live_context_transition_manifest.json")
    )["items"]
    source_role_audit = read_json(
        research / "historical_context_source_role_audit.json"
    )["items"]
    curated = read_json(
        research / "historical_context_source_curated_evidence.json"
    )["items"]
    independently_reviewed_ids = {
        quote_id
        for quote_id, item in curated.items()
        if any(
            str(source.get("evidence_origin") or "").startswith(
                "independently_reviewed_"
            )
            for source in item.get("sources", [])
        )
    }
    newly_completed = {
        "0a67f403a7ac02347e43791d2daf3057aabdcfd64b62edbe1b3484a3a4b66729"
    }
    assert set(frozen) == set(packets) - newly_completed
    assert (
        set(transition) | set(mtf_corpus_transition) | set(mtf_live_transition)
        == independently_reviewed_ids
    )
    assert len(transition) == 12
    for quote_id, packet in packets.items():
        actual = v1.format_context_reply_v2(packet)
        if quote_id in newly_completed:
            assert actual is not None
            continue
        expected = frozen[quote_id]
        assert actual is not None
        expected_text = expected["text"]
        if quote_id in corrections:
            correction = corrections[quote_id]
            correction_field = correction["field"]
            if correction_field not in {"intended_argument", "historical_context"}:
                pytest.fail(f"unsupported correction field: {correction_field}")
            raw_value = raw_packets[quote_id][correction_field]
            assert hashlib.sha256(raw_value.encode()).hexdigest() == (
                correction["original_value_sha256"]
            )
            assert hashlib.sha256(
                correction["corrected_value"].encode()
            ).hexdigest() == correction["corrected_value_sha256"]
            assert packet[correction_field] == correction["corrected_value"]
            if correction_field == "intended_argument":
                frozen_value = expected["field_provenance"]["meaning"][
                    "intended_argument"
                ]
                assert hashlib.sha256(frozen_value.encode()).hexdigest() == (
                    correction["original_value_sha256"]
                )
                assert expected_text.count(frozen_value) == 1
                expected_text = expected_text.replace(
                    frozen_value,
                    correction["corrected_value"],
                    1,
                )
            else:
                assert quote_id in mtf_live_transition
                assert raw_packets[quote_id]["immediate_subject"] == raw_value
                assert packet["historical_context"] == correction["corrected_value"]
                assert packet["immediate_subject"] == correction["corrected_value"]
                assert raw_value not in expected_text
        if quote_id in mtf_live_transition:
            reviewed = mtf_live_transition[quote_id]
            assert actual["source_omitted"] is False
            assert actual["formatter_version"] == v1.HISTORICAL_CONTEXT_FORMATTER_V2
            assert reviewed["quote_text_sha256"] == quote_id
            assert reviewed["current_public_context_supported_fields"] == (
                source_role_audit[quote_id][
                    "public_context_supported_fields"
                ]
            )
            assert len(reviewed["source_bindings"]) == 1
            assert reviewed["source_bindings"][0]["source_id"] in {
                source["source_id"]
                for source in source_role_audit[quote_id]["curated_sources"]
            }
            continue
        if quote_id in transition:
            reviewed = transition[quote_id]
            assert hashlib.sha256(
                expected["text"].encode()
            ).hexdigest() == reviewed[
                "frozen_formatter_trial_text_sha256"
            ]
            assert hashlib.sha256(
                expected_text.encode()
            ).hexdigest() == reviewed[
                "meaning_corrected_frozen_text_sha256"
            ]
            assert hashlib.sha256(
                actual["text"].encode()
            ).hexdigest() == reviewed[
                "current_v9_formatter_text_sha256"
            ]
            assert bool(quote_id in corrections) is reviewed[
                "meaning_correction_applied"
            ]
            assert reviewed["canonical_packet_changed_fields"]
        elif quote_id in statecraft_transition:
            assert actual["text"] != expected_text
            assert actual["source"]["title"] == packet["stable_locator"]
            assert actual["source"]["url"] == ""
            assert actual["source_omitted"] is False
            assert actual["formatter_version"] == v1.HISTORICAL_CONTEXT_FORMATTER_V2
            continue
        elif quote_id in mtf_corpus_transition:
            reviewed = mtf_corpus_transition[quote_id]
            assert actual["source_omitted"] is False
            assert actual["formatter_version"] == v1.HISTORICAL_CONTEXT_FORMATTER_V2
            assert reviewed["quote_text_sha256"] == quote_id
            assert reviewed["current_public_context_supported_fields"] == (
                source_role_audit[quote_id][
                    "public_context_supported_fields"
                ]
            )
            assert len(reviewed["source_bindings"]) == 1
            assert reviewed["source_bindings"][0]["source_id"] in {
                source["source_id"]
                for source in source_role_audit[quote_id]["curated_sources"]
            }
            continue
        else:
            assert actual["text"] == expected_text
        assert actual["template_variant"] == expected["template_variant"]
        assert actual["meaning_included"] == expected["meaning_included"]
        reason_change = REVIEWED_MEANING_REASON_CHANGES.get(quote_id)
        if reason_change is None:
            assert (
                actual["meaning_decision_reason"]
                == expected["meaning_decision_reason"]
            )
        else:
            assert expected["meaning_decision_reason"] == reason_change["before"]
            assert actual["meaning_decision_reason"] == reason_change["after"]
            assert quote_id in corrections
            assert quote_id not in transition
        assert actual["formatter_version"] == v1.HISTORICAL_CONTEXT_FORMATTER_V2


@pytest.mark.parametrize("raw,expected", [
    ("1978-03-30", "30 March 1978"),
    ("March 30, 1978", "30 March 1978"),
    ("30 March 1978", "30 March 1978"),
    ("Unknown", ""),
    ("Unknown (published 1989)", "published in 1989"),
    ("1980s (exact date unknown)", "1980s"),
])
def test_british_date_formatting(raw, expected):
    assert format_british_date(raw) == expected


def test_v2_compact_fluent_layout_and_metadata():
    result = format_context_reply_v2(_packet())
    assert result["text"].startswith("Context — Speech to an audience, 30 March 1978: ")
    assert "Occasion:" not in result["text"] and "Date:" not in result["text"]
    assert "Immediate context:" not in result["text"] and "Historical context" not in result["text"]
    assert "\n\nVerification — Exact wording\n\nSource — " in result["text"]
    assert result["formatter_version"] == FORMATTER_V2
    assert result["weighted_character_count"] == v1.x_weighted_length(result["text"])


def test_meaning_is_omitted_for_documented_direct_restatement():
    packet = _packet()
    result = format_context_reply_v2(packet)
    assert result["meaning_included"] is False
    assert "direct, self-contained proposition" in result["meaning_decision_reason"]
    assert "Meaning —" not in result["text"]


@pytest.mark.parametrize("updates,reason", [
    ({"quote_text": "This was the turning point."}, "context-dependent reference"),
    ({"verification_status": "unverified"}, "non-exact or uncertain"),
    ({"quote_text": "Freedom survives only if power is limited, despite temptations towards centralisation."}, "counter-intuitive or contrastive"),
])
def test_meaning_is_retained_when_historically_necessary(updates, reason):
    result = format_context_reply_v2(_packet(**updates))
    assert result["meaning_included"] is True
    assert reason in result["meaning_decision_reason"]
    assert "Meaning —" in result["text"]


def test_meaning_decision_is_deterministic():
    packet = _packet()
    assert meaning_decision(packet, "Supported context.") == meaning_decision(packet, "Supported context.")


def test_source_and_verification_are_preserved_for_complete_corpus(prepared):
    rows = read_json(prepared / "complete_pairwise_renderings.json")["items"]
    assert len(rows) == 627
    for row in rows.values():
        assert row["v1"]["source"] == row["v2"]["source"]
        assert row["v1"]["verification_label"] == row["v2"]["verification_label"]
        assert not _forbidden_candidate_text(row["v2"]["text"])


def test_all_v2_context_lines_use_british_date_order(prepared):
    rows = read_json(prepared / "complete_pairwise_renderings.json")["items"]
    american_date = __import__("re").compile(
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}\b"
    )
    assert not [qid for qid, row in rows.items() if american_date.search(row["v2"]["text"].split("\n\n", 1)[0])]


def test_strict_provenance_audit_passes(prepared):
    audit = strict_audit(prepared)
    assert audit == {"passed": True, "errors": [], "eligible_count": 627, "sample_count": 50}
    parity = read_json(prepared / "provenance_parity_audit.json")
    assert parity["blocking_regression_count"] == 0 and parity["passed"]


def test_deterministic_stratified_sample_and_no_duplicate_families(prepared):
    rows = read_json(prepared / "complete_pairwise_renderings.json")["items"]
    first = select_review_sample(rows, 50); second = select_review_sample(rows, 50)
    assert first == second and len(first) == 50 and len({row["quote_id"] for row in first}) == 50
    texts = [rows[row["quote_id"]]["quote_text"] for row in first]
    assert not any(_near_duplicate(texts[i], texts[j]) for i in range(50) for j in range(i))
    longest = {row["quote_id"] for row in sorted(rows.values(), key=lambda row: (-row["v1_weighted_count"], row["quote_id"]))[:10]}
    assert longest <= {row["quote_id"] for row in first}
    assert {row["quote_id"] for row in first[:10]} != longest
    assert {rows[row["quote_id"]]["meaning_included"] for row in first} == {True, False}


def test_all_candidate_variants_are_exercised_by_corpus(prepared):
    rows = read_json(prepared / "complete_pairwise_renderings.json")["items"]
    assert {row["v2_template_variant"] for row in rows.values()} == {
        "compact_with_meaning", "compact_without_redundant_meaning",
        "compact_uncertain_wording", "compact_no_public_url",
    }


def test_blind_assignment_is_stable_and_browser_payload_leaks_no_identity(prepared):
    sample = read_json(prepared / "review_sample_50.json")["items"]
    assert build_blind_assignments(sample) == build_blind_assignments(sample)
    rows = read_json(prepared / "complete_pairwise_renderings.json")["items"]
    blind = read_json(prepared / "blind_assignment_manifest.json")["items"]
    qid = sample[0]["quote_id"]
    payload = blind_presentations(rows[qid], blind[qid])
    assert set(payload) == {"a", "b"}
    assert "v1" not in json.dumps(payload) and "v2" not in json.dumps(payload)


def test_review_autosave_idempotence_revision_and_restart_persistence(prepared, tmp_path):
    trial = tmp_path / "trial"; shutil.copytree(prepared, trial)
    qid = read_json(trial / "review_sample_50.json")["items"][0]["quote_id"]
    first, changed = save_review(trial, qid, "a", ["clearer"], "First note")
    same, changed_again = save_review(trial, qid, "a", ["clearer"], "First note")
    revised, revised_changed = save_review(trial, qid, "b", ["more concise"], "Revised")
    assert changed and not changed_again and revised_changed
    assert first["revision"] == same["revision"] == 1 and revised["revision"] == 2
    assert read_json(trial / "human_reviews.json")["items"][qid]["decision"] == "b"
    audit = (trial / "human_review_audit.jsonl").read_text().splitlines()
    assert len(audit) == 2 and json.loads(audit[-1])["previous_value"]["decision"] == "a"


def test_review_results_unblind_and_group_without_changing_assignments(prepared, tmp_path):
    trial = tmp_path / "trial"; shutil.copytree(prepared, trial)
    sample = read_json(trial / "review_sample_50.json")["items"]
    blind_before = (trial / "blind_assignment_manifest.json").read_bytes()
    for index, row in enumerate(sample[:4]):
        save_review(trial, row["quote_id"], ("a", "b", "equal", "neither")[index], ["clearer"])
    results = generate_review_results(trial)
    assert results["reviewed_count"] == 4 and results["unreviewed_count"] == 46
    assert sum(results[key] for key in ("v1_wins", "v2_wins", "equal", "neither")) == 4
    assert results["results_by_verification_label"] and results["consistency_checks"]["unique_reviewed_quote_ids"]
    assert (trial / "blind_assignment_manifest.json").read_bytes() == blind_before


def test_strict_audit_detects_stored_render_tampering(prepared, tmp_path):
    trial = tmp_path / "trial"; shutil.copytree(prepared, trial)
    document = read_json(trial / "complete_pairwise_renderings.json")
    qid = next(iter(document["items"])); document["items"][qid]["v2_sha256"] = "0" * 64
    (trial / "complete_pairwise_renderings.json").write_text(json.dumps(document))
    audit = strict_audit(trial)
    assert not audit["passed"] and any("v2" in error for error in audit["errors"])


def test_prepare_writes_only_requested_trial_directory(tmp_path, historical_corpus_root):
    production = [Path("mrsMThatcher_state.json"), Path("historical_context_reply_history.json"), Path("historical_context_reply_receipt.json")]
    before = {str(path): path.read_bytes() if path.exists() else None for path in production}
    prepare_trial(historical_corpus_root / RESEARCH_RELATIVE, tmp_path / "trial", 50)
    after = {str(path): path.read_bytes() if path.exists() else None for path in production}
    assert before == after


def test_trial_module_has_no_external_client_dependency():
    source = Path("semantic_alignment/historical_context_formatter_trial.py").read_text()
    assert "requests." not in source and "urllib.request" not in source
    assert "import openai" not in source.lower() and "import xai" not in source.lower()
    assert "from google" not in source.lower() and "google.genai" not in source.lower()


def test_review_ui_has_keyboard_shortcuts_and_queued_autosave():
    source = Path("semantic_alignment/historical_context_formatter_trial.py").read_text()
    assert "'1234'.includes(event.key)" in source
    assert "event.key==='ArrowLeft'" in source and "event.key==='ArrowRight'" in source
    assert "if(saving){{queued=true;return}}" in source and "while(queued)" in source

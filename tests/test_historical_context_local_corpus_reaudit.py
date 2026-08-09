from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path

import pytest

import historical_context_local_corpus_reaudit as reaudit
import historical_context_search_research as research
from historical_context_local_archive import LocalArchiveMirror, LocalMTFDocumentIndex


ROOT = Path(__file__).resolve().parents[1]


def mirror_root(tmp_path: Path) -> Path:
    root = tmp_path / "mirror"
    (root / "www.margaretthatcher.org" / "document").mkdir(parents=True)
    return root


def html(document_id: str, article: str, *, author: str = "Margaret Thatcher") -> bytes:
    filler = "Context establishing a sufficiently substantial archive transcript. " * 3
    return f"""<!doctype html><html><head>
<title>Speech to a Conservative audience | Margaret Thatcher Foundation</title>
<link rel="canonical" href="https://www.margaretthatcher.org/document/{document_id}">
</head><body><header class="document-header">
<h1>Speech to a Conservative audience</h1><div class="docauthor">{author}</div>
<div class="docdate"><time datetime="1980-01-26">1980 Jan 26</time></div>
</header><article class="document-body"><p>{filler}</p>{article}<p>{filler}</p></article>
</body></html>""".encode()


def write_document(root: Path, document_id: str, article: str) -> Path:
    path = root / "www.margaretthatcher.org" / "document" / document_id
    path.write_bytes(html(document_id, article))
    return path


def target(quotation: str) -> dict:
    return {
        "quote_id": hashlib.sha256(quotation.encode()).hexdigest(),
        "quotation_text": quotation,
        "recorded_variants": [],
        "distinctive_fragments": [],
        "deterministic_clauses": [],
    }


def classification_target(quotation: str, *, variants: list[str] | None = None) -> dict:
    return {
        **target(quotation),
        "recorded_variants": variants or [],
        "current_gate_status": "allowed",
        "current_gate_disposition": "not_reviewed_open",
        "current_gate_review": {},
    }


def primary_candidate(
    quotation: str,
    *,
    document_id: str = "200000",
    date: str = "1981-02-03",
    event: str = "Speech at a second event",
    passage: str | None = None,
) -> dict:
    return {
        "candidate_mtf_document_id": document_id,
        "canonical_public_url": (
            f"https://www.margaretthatcher.org/document/{document_id}"
        ),
        "accepted_as_primary_evidence": True,
        "speaker_author_evidence": {"verified": True},
        "match_type": "exact quotation",
        "candidate_classification": "strong_primary_evidence",
        "cross_speaker_join_rejected": False,
        "document_date_evidence": date,
        "document_event_evidence": event,
        "supporting_passage": passage or quotation,
        "surrounding_context": "Substantial surrounding primary context.",
    }


def current_claim(
    quotation: str,
    *,
    document_id: str = "100000",
    date: str = "1980-01-26",
    event: str = "Speech at the first event",
    verified_text: str | None = None,
) -> dict:
    return {
        "quotation_text": quotation,
        "verified_text": verified_text if verified_text is not None else quotation,
        "speaker": "Margaret Thatcher",
        "source_event": event,
        "date": date,
        "stable_locator": (
            f"Margaret Thatcher Foundation Document {document_id}"
            if document_id else ""
        ),
        "current_occurrence_direct_mtf_document_ids": (
            [document_id] if document_id else []
        ),
        "known_evidence_direct_mtf_document_ids": (
            [document_id] if document_id else []
        ),
        "known_evidence_direct_mtf_public_urls": (
            [f"https://www.margaretthatcher.org/document/{document_id}"]
            if document_id else []
        ),
        "independently_inspected_mtf_document_ids": [],
    }


def test_derives_all_current_611_eligible_quotes() -> None:
    derived = reaudit.derive_eligible_targets(ROOT, prepare_search_strategy=False)
    assert len(derived["targets"]) == 611
    assert len({row["quote_id"] for row in derived["targets"]}) == 611
    assert all(row["quotation_text"] for row in derived["targets"])


def test_network_denial_blocks_dns_and_connections() -> None:
    with reaudit.deny_network(), pytest.raises(reaudit.ReauditError, match="network access"):
        socket.getaddrinfo("example.com", 443)


def test_local_candidate_ranking_is_deterministic_and_deduplicated(tmp_path: Path) -> None:
    root = mirror_root(tmp_path)
    quotation = "Political myths cherished by commentators die hard."
    write_document(root, "103384", f"<p>{quotation}</p>")
    write_document(root, "103385", f"<p>{quotation}</p>")
    index = LocalMTFDocumentIndex(LocalArchiveMirror(root, maximum_bytes=1024 * 1024))
    first = index.discover(quotation)["results"]
    second = index.discover(quotation)["results"]
    assert first == second
    assert [row["document_id"] for row in first] == ["103384", "103385"]
    assert len({row["document_id"] for row in first}) == len(first)


def test_verify_candidate_requires_actual_regular_validated_file(tmp_path: Path) -> None:
    root = mirror_root(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(html("103384", "<p>Political myths die hard.</p>"))
    (root / "www.margaretthatcher.org/document/103384").symlink_to(outside)
    mirror = LocalArchiveMirror(root, maximum_bytes=1024 * 1024)
    with pytest.raises(Exception, match="regular file|escapes its host directory"):
        reaudit.verify_candidate(
            mirror,
            target("Political myths die hard."),
            {"document_id": "103384", "result_url": "https://www.margaretthatcher.org/document/103384"},
        )


def test_speaker_separation_rejects_words_joined_across_contributions(tmp_path: Path) -> None:
    root = mirror_root(tmp_path)
    quotation = "Freedom cannot be divided."
    body = html(
        "103384",
        "<p>INTERVIEWER: Freedom cannot</p><p>MRS THATCHER: be divided.</p>",
    )
    path = root / "www.margaretthatcher.org/document/103384"
    path.write_bytes(body)
    validation = research.inspect_mtf_document(
        "https://www.margaretthatcher.org/document/103384", "text/html", body
    )
    extraction = research.extract_page_text({
        "status": "fetched", "content_type": "text/html", "body": body,
        "mtf_document_validation": validation,
    })
    match, speaker = reaudit.contribution_aware_match(
        target(quotation), extraction, body, validation
    )
    assert match["match_type"] == "assembled_clauses"
    assert match["cross_speaker_join_rejected"] is True
    assert speaker["verified"] is False


def test_explicit_thatcher_contribution_verifies_speaker(tmp_path: Path) -> None:
    quotation = "Freedom cannot be divided."
    body = html(
        "103384",
        "<p>INTERVIEWER: What do you say?</p><p>MRS THATCHER: Freedom cannot be divided.</p>",
    )
    validation = research.inspect_mtf_document(
        "https://www.margaretthatcher.org/document/103384", "text/html", body
    )
    extraction = research.extract_page_text({
        "status": "fetched", "content_type": "text/html", "body": body,
        "mtf_document_validation": validation,
    })
    match, speaker = reaudit.contribution_aware_match(
        target(quotation), extraction, body, validation
    )
    assert match["match_type"] == "exact_quotation"
    assert speaker["verified"] is True
    assert speaker["speaker_label"] == "MRS THATCHER"


def test_unknown_is_not_an_authorised_variant() -> None:
    filtered = reaudit._semantic_match_target(
        classification_target(
            "Freedom cannot be divided.", variants=[" Unknown... "]
        )
    )
    assert filtered["recorded_variants"] == []
    assert filtered["placeholder_variant_rejection_count"] == 1


def test_placeholder_variant_in_archive_header_is_rejected() -> None:
    quotation = "Freedom cannot be divided."
    body = html("103384", "<p>A wholly unrelated archive passage.</p>").replace(
        b"Speech to a Conservative audience", b"Unknown"
    )
    validation = research.inspect_mtf_document(
        "https://www.margaretthatcher.org/document/103384", "text/html", body
    )
    extraction = research.extract_page_text({
        "status": "fetched", "content_type": "text/html", "body": body,
        "mtf_document_validation": validation,
    })
    match, speaker = reaudit.contribution_aware_match(
        classification_target(quotation, variants=["unknown"]),
        extraction,
        body,
        validation,
    )
    assert match["match_type"] == "none"
    assert speaker["verified"] is True


def test_zero_lexical_overlap_cannot_be_recorded_variant_evidence() -> None:
    filtered = reaudit._semantic_match_target(
        classification_target(
            "Freedom cannot be divided.", variants=["Political mythology"]
        )
    )
    assert filtered["recorded_variants"] == []
    assert filtered["lexically_unrelated_variant_rejection_count"] == 1


def test_one_shared_content_word_does_not_authorise_long_variant() -> None:
    filtered = reaudit._semantic_match_target(classification_target(
        "Enterprise flourishes when taxation falls and individual choice expands.",
        variants=[
            "Diplomatic negotiations continued through winter while regional taxation records were archived."
        ],
    ))
    assert filtered["recorded_variants"] == []
    diagnostic = filtered["variant_relationship_diagnostics"][0]
    assert diagnostic["reason"] == "fewer_than_two_shared_content_tokens"
    assert diagnostic["shared_content_token_count"] == 1


def test_two_shared_words_with_negligible_long_overlap_are_rejected() -> None:
    filtered = reaudit._semantic_match_target(classification_target(
        "Freedom and enterprise require courage responsibility incentives markets choice opportunity prosperity and national renewal.",
        variants=[
            "A lengthy diplomatic memorandum discusses freedom enterprise treaties borders committees ambassadors negotiations security alliances and protocol."
        ],
    ))
    assert filtered["recorded_variants"] == []
    diagnostic = filtered["variant_relationship_diagnostics"][0]
    assert diagnostic["shared_content_token_count"] == 2
    assert diagnostic["content_overlap"] < diagnostic["minimum_content_overlap"]


def test_contraction_and_punctuation_variant_is_authorised() -> None:
    variant = "Freedom can't endure unless we defend individual responsibility!"
    filtered = reaudit._semantic_match_target(classification_target(
        "Freedom cannot endure unless we defend individual responsibility.",
        variants=[variant],
    ))
    assert filtered["recorded_variants"] == [variant]
    assert filtered["variant_relationship_diagnostics"][0]["accepted"] is True


def test_substantive_variant_with_strong_token_overlap_is_authorised() -> None:
    variant = (
        "We should reduce excessive taxation so enterprise can create greater "
        "prosperity across Britain."
    )
    filtered = reaudit._semantic_match_target(classification_target(
        "We must reduce taxation so that enterprise can create prosperity throughout Britain.",
        variants=[variant],
    ))
    assert filtered["recorded_variants"] == [variant]
    diagnostic = filtered["variant_relationship_diagnostics"][0]
    assert diagnostic["shared_content_token_count"] >= 2
    assert diagnostic["word_sequence_similarity"] >= 0.5


def test_zero_wording_similarity_cannot_support_primary_variant() -> None:
    quotation = "Freedom cannot be divided."
    candidate = {
        **primary_candidate(quotation, passage="Freedom must remain indivisible."),
        "match_type": "recorded variant",
        "wording_similarity": 0.0,
    }
    categories, _proposed, _unblock, _reason = reaudit.classify_changes(
        classification_target(
            quotation, variants=["Freedom must remain indivisible."]
        ),
        candidate,
        current_claim(quotation),
    )
    assert reaudit.CATEGORY_STRONGER_EVIDENCE not in categories
    assert reaudit.CATEGORY_WORDING_CORRECTION not in categories


@pytest.mark.parametrize(
    ("heading", "quotation"),
    [
        ("Diane Sawyer, CBS", "If you want something said, ask a man; if you want something done, ask a woman."),
        ("Sir Robin Day", "I am not a consensus politician. I am a conviction politician."),
    ],
)
def test_named_interviewer_quotation_is_not_thatcher_speech(
    heading: str, quotation: str
) -> None:
    body = html(
        "103384",
        f"<p>{heading}</p><p>{quotation}</p>"
        "<p>Prime Minister</p><p>That is not how I would put the matter.</p>",
    )
    validation = research.inspect_mtf_document(
        "https://www.margaretthatcher.org/document/103384", "text/html", body
    )
    extraction = research.extract_page_text({
        "status": "fetched", "content_type": "text/html", "body": body,
        "mtf_document_validation": validation,
    })
    match, speaker = reaudit.contribution_aware_match(
        target(quotation), extraction, body, validation
    )
    assert match["match_type"] == "exact_quotation"
    assert speaker["speaker_class"] == "other"
    assert speaker["verified"] is False
    fields = reaudit._fresh_semantic_fields(
        classification_target(quotation),
        url="https://www.margaretthatcher.org/document/103384",
        body=body,
        validation=validation,
        extraction=extraction,
        reverified=True,
    )
    assert fields["accepted_as_primary_evidence"] is False
    assert fields["candidate_semantic_reverification_status"] == (
        "reverified_rejected_speaker"
    )


def test_standalone_prime_minister_contribution_is_accepted() -> None:
    quotation = "Freedom cannot be divided."
    body = html(
        "103384",
        "<p>Interviewer</p><p>What is your answer?</p>"
        f"<p>Prime Minister</p><p>{quotation}</p>",
    )
    validation = research.inspect_mtf_document(
        "https://www.margaretthatcher.org/document/103384", "text/html", body
    )
    extraction = research.extract_page_text({
        "status": "fetched", "content_type": "text/html", "body": body,
        "mtf_document_validation": validation,
    })
    match, speaker = reaudit.contribution_aware_match(
        target(quotation), extraction, body, validation
    )
    assert match["match_type"] == "exact_quotation"
    assert speaker["verified"] is True
    assert speaker["speaker_label"] == "Prime Minister"
    fields = reaudit._fresh_semantic_fields(
        classification_target(quotation),
        url="https://www.margaretthatcher.org/document/103384",
        body=body,
        validation=validation,
        extraction=extraction,
        reverified=True,
    )
    assert fields["accepted_as_primary_evidence"] is True
    assert fields["candidate_semantic_reverification_status"] == (
        "reverified_accepted"
    )


def test_thatcher_article_section_heading_preserves_document_authorship() -> None:
    quotation = "Freedom cannot be divided."
    body = html("103384", f"<h2>Economic Policy</h2><p>{quotation}</p>")
    validation = research.inspect_mtf_document(
        "https://www.margaretthatcher.org/document/103384", "text/html", body
    )
    extraction = research.extract_page_text({
        "status": "fetched", "content_type": "text/html", "body": body,
        "mtf_document_validation": validation,
    })
    match, speaker = reaudit.contribution_aware_match(
        target(quotation), extraction, body, validation
    )
    assert match["match_type"] == "exact_quotation"
    assert speaker["verified"] is True
    assert speaker["evidence_basis"] == "explicit_document_author"
    assert speaker["labels_detected"] is False


def test_all_capital_and_ordinary_article_headings_preserve_authorship() -> None:
    quotation = "Freedom cannot be divided."
    body = html(
        "103384",
        "<h2>ELECTION ISSUES</h2><p>Opening discussion.</p>"
        f"<h3>Income Tax</h3><p>{quotation}</p>",
    )
    validation = research.inspect_mtf_document(
        "https://www.margaretthatcher.org/document/103384", "text/html", body
    )
    segmentation = reaudit.speaker_segments(body, validation)
    assert segmentation["labels_detected"] is False
    assert segmentation["segments"][0]["speaker_class"] == "thatcher"


def test_name_in_h2_does_not_attribute_commentary_to_thatcher() -> None:
    quotation = "Freedom cannot be divided."
    body = html(
        "103384", f"<h2>Margaret Thatcher</h2><p>{quotation}</p>",
        author="Independent Commentator",
    )
    validation = research.inspect_mtf_document(
        "https://www.margaretthatcher.org/document/103384", "text/html", body
    )
    extraction = research.extract_page_text({
        "status": "fetched", "content_type": "text/html", "body": body,
        "mtf_document_validation": validation,
    })
    match, speaker = reaudit.contribution_aware_match(
        target(quotation), extraction, body, validation
    )
    assert match["match_type"] == "exact_quotation"
    assert speaker["verified"] is False
    assert speaker["labels_detected"] is False


def test_ordinary_phrases_are_not_person_labels() -> None:
    body = html(
        "103384",
        "<p>So just let me plunge in quickly</p><p>Income Tax</p>"
        "<p>Freedom cannot be divided.</p>",
    )
    validation = research.inspect_mtf_document(
        "https://www.margaretthatcher.org/document/103384", "text/html", body
    )
    segmentation = reaudit.speaker_segments(body, validation)
    assert segmentation["labels_detected"] is False
    assert segmentation["segments"][0]["speaker_class"] == "thatcher"


def test_thatcher_variant_beats_interviewer_exact_wording() -> None:
    quotation = (
        "Freedom cannot endure unless we defend individual responsibility."
    )
    variant = "Freedom can't endure unless we defend individual responsibility."
    body = html(
        "103384",
        f"<p>Interviewer</p><p>{quotation}</p>"
        f"<p>Prime Minister</p><p>{variant}</p>",
    )
    validation = research.inspect_mtf_document(
        "https://www.margaretthatcher.org/document/103384", "text/html", body
    )
    extraction = research.extract_page_text({
        "status": "fetched", "content_type": "text/html", "body": body,
        "mtf_document_validation": validation,
    })
    match, speaker = reaudit.contribution_aware_match(
        classification_target(quotation, variants=[variant]),
        extraction,
        body,
        validation,
    )
    assert match["match_type"] == "recorded_variant"
    assert reaudit.word_tokens(match["supporting_passage"]) == (
        reaudit.word_tokens(variant)
    )
    assert match["stronger_non_thatcher_occurrence"]["match_type"] == (
        "exact_quotation"
    )
    assert speaker["verified"] is True
    fields = reaudit._fresh_semantic_fields(
        classification_target(quotation, variants=[variant]),
        url="https://www.margaretthatcher.org/document/103384",
        body=body,
        validation=validation,
        extraction=extraction,
        reverified=True,
    )
    assert fields["accepted_as_primary_evidence"] is True


def test_interviewer_exact_without_thatcher_support_is_rejected_for_speaker() -> None:
    quotation = "Freedom cannot be divided."
    body = html(
        "103384",
        f"<p>Interviewer</p><p>{quotation}</p>"
        "<p>Prime Minister</p><p>I would answer a different question.</p>",
    )
    validation = research.inspect_mtf_document(
        "https://www.margaretthatcher.org/document/103384", "text/html", body
    )
    extraction = research.extract_page_text({
        "status": "fetched", "content_type": "text/html", "body": body,
        "mtf_document_validation": validation,
    })
    fields = reaudit._fresh_semantic_fields(
        classification_target(quotation),
        url="https://www.margaretthatcher.org/document/103384",
        body=body,
        validation=validation,
        extraction=extraction,
        reverified=True,
    )
    assert fields["match_type"] == "exact quotation"
    assert fields["speaker_author_evidence"]["speaker_class"] == "other"
    assert fields["accepted_as_primary_evidence"] is False
    assert fields["candidate_semantic_reverification_status"] == (
        "reverified_rejected_speaker"
    )


def test_thatcher_exact_is_preferred_when_both_speakers_repeat_wording() -> None:
    quotation = "Freedom cannot be divided."
    body = html(
        "103384",
        f"<p>Interviewer</p><p>{quotation}</p>"
        f"<p>Prime Minister</p><p>{quotation}</p>",
    )
    validation = research.inspect_mtf_document(
        "https://www.margaretthatcher.org/document/103384", "text/html", body
    )
    extraction = research.extract_page_text({
        "status": "fetched", "content_type": "text/html", "body": body,
        "mtf_document_validation": validation,
    })
    match, speaker = reaudit.contribution_aware_match(
        target(quotation), extraction, body, validation
    )
    assert match["match_type"] == "exact_quotation"
    assert speaker["verified"] is True
    assert speaker["speaker_label"] == "Prime Minister"


def test_normal_prose_beginning_with_person_name_is_not_a_heading() -> None:
    quotation = "Hugo Young described the argument before turning to policy."
    body = html("103384", f"<p>{quotation}</p>")
    validation = research.inspect_mtf_document(
        "https://www.margaretthatcher.org/document/103384", "text/html", body
    )
    segmentation = reaudit.speaker_segments(body, validation)
    assert segmentation["labels_detected"] is False
    assert segmentation["segments"][0]["speaker_class"] == "thatcher"


def test_private_paths_are_rejected_from_generated_values(tmp_path: Path) -> None:
    archive = tmp_path / "private-archive"
    run = tmp_path / "private-run"
    with pytest.raises(reaudit.ReauditError, match="private absolute path"):
        reaudit.assert_no_private_path_disclosure(
            [{"diagnostic": str(archive / "document/1")}], archive, run
        )


def test_blocked_unblock_logic_requires_all_admission_evidence() -> None:
    resolved, _reason = reaudit._blocking_reason_resolved(
        "insufficient_to_assess",
        "Attribution and wording cannot be assessed because no primary transcript is retained.",
        speaker_verified=True,
        wording_verified=True,
        context_date_source_sufficient=True,
    )
    assert resolved is True
    not_resolved, _reason = reaudit._blocking_reason_resolved(
        "future_correction_needed",
        "Meaning adds an unsupported interpretation.",
        speaker_verified=True,
        wording_verified=True,
        context_date_source_sufficient=True,
    )
    assert not_resolved is False


def test_no_support_blocked_candidate_does_not_verify_speaker() -> None:
    quotation = "Freedom cannot be divided."
    quote_id = target(quotation)["quote_id"]
    blocked_target = {
        **classification_target(quotation),
        "quote_id": quote_id,
        "current_gate_status": "blocked",
        "current_gate_disposition": "insufficient_to_assess",
        "current_gate_review": {"reason": "Primary wording is not verified."},
    }
    candidate = {
        **primary_candidate(quotation),
        "quote_id": quote_id,
        "accepted_as_primary_evidence": False,
        "match_type": "no support",
        "wording_similarity": 0.0,
        "supporting_passage": "",
        "speaker_author_evidence": {
            "verified": True,
            "evidence_basis": "explicit_document_author",
        },
        "candidate_semantic_reverification_status": (
            "reverified_rejected_no_support"
        ),
        "proposed_unblock": False,
        "proposed_unblock_rationale": "wording is not verified",
    }
    result = reaudit.reassess_blocked([blocked_target], [candidate])[0]
    assert result["speaker_attribution_verified"] is False
    assert result["exact_or_acceptable_primary_variant_verified"] is False


def test_evidence_complete_semantic_review_is_advisory_not_unblocked() -> None:
    quotation = "Freedom cannot be divided."
    quote_id = target(quotation)["quote_id"]
    blocked_target = {
        **classification_target(quotation),
        "quote_id": quote_id,
        "current_gate_status": "blocked",
        "current_gate_disposition": "future_correction_needed",
        "current_gate_review": {
            "reason": "Published meaning adds an unsupported interpretation requiring semantic review."
        },
    }
    candidate = {
        **primary_candidate(quotation),
        "quote_id": quote_id,
        "wording_similarity": 1.0,
        "candidate_semantic_reverification_status": "reverified_accepted",
        "proposed_unblock": False,
        "proposed_unblock_rationale": (
            "the source finding does not resolve the meaning issue"
        ),
    }
    result = reaudit.reassess_blocked([blocked_target], [candidate])[0]
    assert result[
        "evidence_complete_but_separate_semantic_review_required"
    ] is True
    assert result["proposed_unblock"] is False
    summary = reaudit.build_summary(
        [blocked_target],
        [candidate],
        [result],
        {"document_count": 1, "total_bytes": 1, "inventory_sha256": "a" * 64},
        {"document_count": 1, "total_bytes": 1, "inventory_sha256": "a" * 64},
        input_hashes_stable=True,
        completed_at="synthetic",
    )
    assert summary["evidence_complete_semantic_review_quote_count"] == 1
    assert summary["evidence_complete_semantic_review_quote_ids"] == [quote_id]


def test_wording_correction_requires_same_current_source_identity() -> None:
    quotation = "Freedom cannot be divided."
    candidate = {
        "candidate_mtf_document_id": "200000",
        "canonical_public_url": "https://www.margaretthatcher.org/document/200000",
        "accepted_as_primary_evidence": True,
        "speaker_author_evidence": {"verified": True},
        "match_type": "exact quotation",
        "candidate_classification": "strong_primary_evidence",
        "cross_speaker_join_rejected": False,
        "document_date_evidence": "1980-01-26",
        "document_event_evidence": "Speech to a Conservative audience",
        "supporting_passage": quotation,
        "surrounding_context": "Substantial surrounding primary context.",
    }
    current = {
        "quotation_text": quotation,
        "verified_text": "Freedom must not be divided.",
        "speaker": "Margaret Thatcher",
        "source_event": "Speech to a Conservative audience",
        "date": "1980-01-26",
        "stable_locator": "Margaret Thatcher Foundation Document 100000",
        "current_occurrence_direct_mtf_document_ids": ["100000"],
        "known_evidence_direct_mtf_document_ids": ["100000"],
        "known_evidence_direct_mtf_public_urls": [
            "https://www.margaretthatcher.org/document/100000"
        ],
        "independently_inspected_mtf_document_ids": [],
    }
    categories, _proposed, _unblock, _reason = reaudit.classify_changes(
        {
            "current_gate_status": "allowed",
            "current_gate_disposition": "not_reviewed_open",
            "current_gate_review": {},
        },
        candidate,
        current,
    )
    assert reaudit.CATEGORY_WORDING_CORRECTION not in categories


def test_second_date_and_event_is_an_additional_primary_occurrence() -> None:
    quotation = "Freedom cannot be divided."
    categories, proposed, _unblock, _reason = reaudit.classify_changes(
        classification_target(quotation),
        primary_candidate(quotation),
        current_claim(quotation),
    )
    assert reaudit.CATEGORY_ADDITIONAL_OCCURRENCE in categories
    assert reaudit.CATEGORY_DATE_CORRECTION not in categories
    assert reaudit.CATEGORY_EVENT_CORRECTION not in categories
    assert "date" not in proposed
    assert "source_event" not in proposed


def test_known_evidence_id_does_not_become_current_occurrence_identity() -> None:
    quotation = "Freedom cannot be divided."
    target = {
        **classification_target(quotation),
        "current_packet": {
            "quote_text": quotation,
            "verified_text": quotation,
            "speaker": "Margaret Thatcher",
            "source_event": "Speech at the first event",
            "date": "1980-01-26",
            "stable_locator": "Margaret Thatcher Foundation Document 100000",
        },
        "current_source_role": {
            "sources": [{
                "canonical_url": (
                    "https://www.margaretthatcher.org/document/200000"
                ),
                "source_event": "Speech at a second event",
                "source_date": "1981-02-03",
            }],
        },
    }
    current = reaudit.current_values(target)
    assert current["current_occurrence_direct_mtf_document_ids"] == ["100000"]
    assert current["known_evidence_direct_mtf_document_ids"] == [
        "100000", "200000"
    ]

    categories, proposed, _unblock, _reason = reaudit.classify_changes(
        target, primary_candidate(quotation), current
    )
    assert reaudit.CATEGORY_ADDITIONAL_OCCURRENCE in categories
    assert reaudit.CATEGORY_DATE_CORRECTION not in categories
    assert reaudit.CATEGORY_EVENT_CORRECTION not in categories
    assert "date" not in proposed
    assert "source_event" not in proposed


def test_model_proposed_same_day_document_cannot_correct_current_occurrence() -> None:
    quotation = "Freedom cannot be divided."
    target = {
        **classification_target(quotation),
        "current_packet": {
            "quote_text": quotation,
            "verified_text": quotation,
            "speaker": "Margaret Thatcher",
            "source_event": "Election-eve speech",
            "date": "1979-05-02",
            "stable_locator": "Margaret Thatcher Foundation Document 100000",
        },
        "current_source_role": {
            "model_proposed_source_leads": [{
                "canonical_url": (
                    "https://www.margaretthatcher.org/document/200000"
                ),
                "source_date": "1979-05-02",
                "source_event": "Daily Telegraph article",
            }],
        },
    }
    current = reaudit.current_values(target)
    candidate = primary_candidate(
        quotation,
        document_id="200000",
        date="1979-05-02",
        event="Daily Telegraph article",
    )
    assert current["current_occurrence_direct_mtf_document_ids"] == ["100000"]
    assert current["known_evidence_direct_mtf_document_ids"] == [
        "100000", "200000"
    ]
    assert reaudit._occurrence_relation(candidate, current) != (
        "same_current_occurrence"
    )
    categories, proposed, _unblock, _reason = reaudit.classify_changes(
        target, candidate, current
    )
    assert reaudit.CATEGORY_EVENT_CORRECTION not in categories
    assert "source_event" not in proposed


def test_date_conflict_prevents_filling_missing_current_event() -> None:
    quotation = "Freedom cannot be divided."
    categories, proposed, _unblock, _reason = reaudit.classify_changes(
        classification_target(quotation),
        primary_candidate(quotation),
        current_claim(quotation, event=""),
    )
    assert reaudit.CATEGORY_ADDITIONAL_OCCURRENCE in categories
    assert reaudit.CATEGORY_DATE_CORRECTION not in categories
    assert reaudit.CATEGORY_EVENT_CORRECTION not in categories
    assert "date" not in proposed
    assert "source_event" not in proposed


def test_event_conflict_prevents_filling_missing_current_date() -> None:
    quotation = "Freedom cannot be divided."
    categories, proposed, _unblock, _reason = reaudit.classify_changes(
        classification_target(quotation),
        primary_candidate(quotation),
        current_claim(quotation, date=""),
    )
    assert reaudit.CATEGORY_ADDITIONAL_OCCURRENCE in categories
    assert reaudit.CATEGORY_DATE_CORRECTION not in categories
    assert reaudit.CATEGORY_EVENT_CORRECTION not in categories
    assert "date" not in proposed
    assert "source_event" not in proposed


def test_missing_current_date_and_event_can_be_filled_from_primary_evidence() -> None:
    quotation = "Freedom cannot be divided."
    categories, proposed, _unblock, _reason = reaudit.classify_changes(
        classification_target(quotation),
        primary_candidate(quotation, document_id="200000"),
        current_claim(quotation, document_id="", date="", event=""),
    )
    assert reaudit.CATEGORY_DATE_CORRECTION in categories
    assert reaudit.CATEGORY_EVENT_CORRECTION in categories
    assert reaudit.CATEGORY_ADDITIONAL_OCCURRENCE not in categories
    assert proposed["date"] == "1981-02-03"
    assert proposed["source_event"] == "Speech at a second event"


def test_placeholder_date_event_and_locator_are_missing_for_classification() -> None:
    quotation = "Freedom cannot be divided."
    values = reaudit.current_values({
        **classification_target(quotation),
        "current_packet": {
            "quote_text": quotation,
            "verified_text": "N/A",
            "speaker": "Margaret Thatcher",
            "source_event": "Not available in primary sources",
            "date": "Unknown",
            "stable_locator": "Unspecified",
        },
        "current_source_role": {},
    })
    assert values["verified_text"] == ""
    assert values["date"] == ""
    assert values["source_event"] == ""
    assert values["stable_locator"] == ""
    assert values["current_occurrence_direct_mtf_document_ids"] == []
    assert values["original_packet_values"]["date"] == "Unknown"
    categories, proposed, _unblock, _reason = reaudit.classify_changes(
        classification_target(quotation),
        primary_candidate(quotation),
        values,
    )
    assert reaudit.CATEGORY_DATE_CORRECTION in categories
    assert reaudit.CATEGORY_EVENT_CORRECTION in categories
    assert reaudit.CATEGORY_LOCATOR_CORRECTION in categories
    assert reaudit.CATEGORY_ADDITIONAL_OCCURRENCE not in categories
    assert proposed["date"] == "1981-02-03"


def test_verified_evidence_corrects_provisional_thatcher_attribution() -> None:
    quotation = "Freedom cannot be divided."
    current = current_claim(quotation)
    current["speaker"] = "Margaret Thatcher (Attributed)"
    categories, proposed, _unblock, _reason = reaudit.classify_changes(
        classification_target(quotation),
        primary_candidate(
            quotation,
            document_id="100000",
            date="1980-01-26",
            event="Speech at the first event",
        ),
        current,
    )
    assert reaudit.CATEGORY_ATTRIBUTION_CORRECTION in categories
    assert proposed["speaker"] == "Margaret Thatcher"


@pytest.mark.parametrize(
    ("current_event", "candidate_event", "date"),
    [
        (
            "House of Commons Debate on the European Community",
            "HC S [European Community]",
            "1991-06-26",
        ),
        (
            "House of Commons Debate on Economic and Industrial Policy",
            "HC S: [Government motion on economic and industrial policy]",
            "1981-02-05",
        ),
    ],
)
def test_same_date_hc_title_variants_are_same_occurrence(
    current_event: str, candidate_event: str, date: str
) -> None:
    quotation = "Freedom cannot be divided."
    current = current_claim(
        quotation, document_id="100000", date=date, event=current_event
    )
    candidate = primary_candidate(
        quotation, document_id="200000", date=date, event=candidate_event
    )
    assert reaudit._occurrence_relation(candidate, current) == "same_current_occurrence"
    categories, _proposed, _unblock, _reason = reaudit.classify_changes(
        classification_target(quotation), candidate, current
    )
    assert reaudit.CATEGORY_ADDITIONAL_OCCURRENCE not in categories


def test_known_same_date_daily_telegraph_document_refines_source() -> None:
    quotation = "Freedom cannot be divided."
    target = {
        **classification_target(quotation),
        "current_packet": {
            "quote_text": quotation,
            "verified_text": quotation,
            "speaker": "Margaret Thatcher",
            "source_event": "Election-eve speech",
            "date": "1979-05-02",
            "stable_locator": "Margaret Thatcher Foundation Document 100000",
        },
        "current_source_role": {
            "renderable_sources": [{
                "canonical_url": (
                    "https://www.margaretthatcher.org/document/200000"
                ),
                "source_date": "1979-05-02",
            }],
        },
    }
    current = reaudit.current_values(target)
    candidate = primary_candidate(
        quotation,
        document_id="200000",
        date="1979-05-02",
        event="Daily Telegraph article",
    )
    categories, proposed, _unblock, _reason = reaudit.classify_changes(
        target, candidate, current
    )
    assert reaudit.CATEGORY_EVENT_CORRECTION in categories
    assert reaudit.CATEGORY_ADDITIONAL_OCCURRENCE not in categories
    assert proposed["source_event"] == "Daily Telegraph article"


def test_same_day_ambiguous_source_is_neutral_review() -> None:
    quotation = "Freedom cannot be divided."
    candidate = primary_candidate(
        quotation,
        document_id="200000",
        date="1980-01-26",
        event="European community policy statement",
    )
    current = current_claim(
        quotation,
        document_id="100000",
        date="1980-01-26",
        event="European industrial policy report",
    )
    categories, proposed, _unblock, _reason = reaudit.classify_changes(
        classification_target(quotation), candidate, current
    )
    assert reaudit.CATEGORY_NEUTRAL_MATCH_REVIEW in categories
    assert reaudit.CATEGORY_ADDITIONAL_OCCURRENCE not in categories
    assert "source_event" not in proposed


def test_exact_excerpt_confirms_without_rewriting_verified_text() -> None:
    quotation = "Freedom cannot be divided."
    passage = "My friends, freedom cannot be divided, and our task is clear."
    categories, proposed, _unblock, _reason = reaudit.classify_changes(
        classification_target(quotation),
        primary_candidate(
            quotation,
            document_id="100000",
            date="1980-01-26",
            event="Speech at the first event",
            passage=quotation,
        ),
        current_claim(quotation, verified_text=passage),
    )
    assert reaudit.CATEGORY_EXACT_EXCERPT_CONFIRMATION in categories
    assert reaudit.CATEGORY_WORDING_CORRECTION not in categories
    assert "verified_text" not in proposed


def test_authoritative_wording_conflict_can_correct_verified_text() -> None:
    quotation = "Freedom cannot be divided."
    categories, proposed, _unblock, _reason = reaudit.classify_changes(
        classification_target(quotation),
        primary_candidate(
            quotation,
            document_id="100000",
            date="1980-01-26",
            event="Speech at the first event",
            passage="She said: Freedom cannot be divided. That remains our policy.",
        ),
        current_claim(
            quotation,
            verified_text="Freedom must not be divided.",
        ),
    )
    assert reaudit.CATEGORY_WORDING_CORRECTION in categories
    assert reaudit.CATEGORY_EXACT_EXCERPT_CONFIRMATION not in categories
    assert proposed["verified_text"] == quotation


def test_cross_speaker_noncontiguous_match_is_neutral_review() -> None:
    quotation = "Freedom cannot be divided."
    candidate = {
        **primary_candidate(quotation),
        "accepted_as_primary_evidence": False,
        "speaker_author_evidence": {"verified": False},
        "match_type": "partial/assembled wording",
        "candidate_classification": "contradictory_evidence",
        "cross_speaker_join_rejected": True,
    }
    categories, proposed, _unblock, _reason = reaudit.classify_changes(
        classification_target(quotation), candidate, current_claim(quotation)
    )
    assert reaudit.CATEGORY_NEUTRAL_MATCH_REVIEW in categories
    assert reaudit.CATEGORY_CONTRADICTION not in categories
    assert "historical_context_confidence" not in proposed


def test_genuine_canonical_claim_conflict_uses_contradiction_category() -> None:
    quotation = "Freedom cannot be divided."
    candidate = {
        **primary_candidate(quotation),
        "accepted_as_primary_evidence": False,
        "match_type": "no support",
        "candidate_classification": "contradictory_evidence",
    }
    categories, proposed, _unblock, _reason = reaudit.classify_changes(
        classification_target(quotation), candidate, current_claim(quotation)
    )
    assert reaudit.CATEGORY_CONTRADICTION in categories
    assert reaudit.CATEGORY_NEUTRAL_MATCH_REVIEW not in categories
    assert "genuine conflict" in proposed["historical_context_confidence"]


def test_inventory_change_detection(tmp_path: Path) -> None:
    root = mirror_root(tmp_path)
    path = write_document(root, "103384", "<p>Political myths die hard.</p>")
    mirror = LocalArchiveMirror(root, maximum_bytes=1024 * 1024)
    before = reaudit.inventory_document(mirror.inventory(), phase="before", timestamp="before")
    path.write_bytes(html("103384", "<p>Political myths change.</p>"))
    after = reaudit.inventory_document(mirror.inventory(), phase="after", timestamp="after")
    assert reaudit.inventory_is_stable(before, after) is False


def test_empty_inventory_is_rejected() -> None:
    with pytest.raises(reaudit.ReauditError, match="no usable numeric"):
        reaudit.require_usable_inventory({
            "document_count": 0,
            "total_bytes": 0,
            "inventory_sha256": hashlib.sha256(b"[]").hexdigest(),
        })


def test_output_writer_cannot_write_canonical_path(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    canonical = tmp_path / "research_packets.json"
    with pytest.raises(reaudit.ReauditError, match="unrecognised"):
        reaudit.write_json(run, canonical.name, {})
    assert not canonical.exists()


def test_authoritative_hash_change_is_rejected() -> None:
    with pytest.raises(reaudit.ReauditError, match="canonical input changed"):
        reaudit.assert_authoritative_inputs_unchanged({"a": "one"}, {"a": "two"})


def test_candidate_json_does_not_contain_mirror_root(tmp_path: Path) -> None:
    root = mirror_root(tmp_path)
    quotation = "Political myths cherished by commentators die hard."
    write_document(root, "103384", f"<p>{quotation}</p>")
    mirror = LocalArchiveMirror(root, maximum_bytes=1024 * 1024)
    synthetic = {
        **target(quotation),
        "current_gate_disposition": "not_reviewed_open",
        "current_gate_status": "allowed",
        "current_unresolved_status": False,
        "current_packet": {
            "quote_text": quotation, "verified_text": quotation,
            "speaker": "Margaret Thatcher", "source_event": "", "date": "",
            "stable_locator": "", "verification_status": "verified",
            "research_confidence": "high",
        },
        "current_source_role": {},
        "current_gate_review": {},
    }
    candidate = reaudit.verify_candidate(
        mirror,
        synthetic,
        {"document_id": "103384", "result_url": "https://www.margaretthatcher.org/document/103384"},
    )
    assert str(root) not in json.dumps(candidate, sort_keys=True)
    assert candidate["actual_regular_file_verified"] is True


def prepare_reclassification_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    project = tmp_path / "project"
    project.mkdir()
    archive = mirror_root(tmp_path)
    quotation = "Political myths cherished by commentators die hard."
    document_path = write_document(root=archive, document_id="103384", article=f"<p>{quotation}</p>")
    synthetic = {
        **classification_target(quotation),
        "current_unresolved_status": False,
        "current_packet": {
            "quote_text": quotation,
            "verified_text": quotation,
            "speaker": "Margaret Thatcher",
            "source_event": "Speech to a Conservative audience",
            "date": "1980-01-26",
            "stable_locator": "Margaret Thatcher Foundation Document 103384",
            "verification_status": "verified",
            "research_confidence": "high",
        },
        "current_source_role": {},
    }
    mirror = LocalArchiveMirror(archive, maximum_bytes=1024 * 1024)
    candidate = reaudit.verify_candidate(
        mirror,
        synthetic,
        {
            "document_id": "103384",
            "result_url": "https://www.margaretthatcher.org/document/103384",
        },
    )
    assert candidate["accepted_as_primary_evidence"] is True

    source = tmp_path / "source-run"
    source.mkdir(mode=0o700)
    source.chmod(0o700)
    before = reaudit.inventory_document(
        mirror.inventory(), phase="before", timestamp="source-before"
    )
    after = {
        **before,
        "phase": "after",
        "run_timestamp": "source-after",
        "document_count": before["document_count"] + 1,
        "inventory_sha256": "f" * 64,
    }
    (source / "archive_inventory_before.json").write_bytes(
        reaudit.canonical_json_bytes(before)
    )
    (source / "archive_inventory_after.json").write_bytes(
        reaudit.canonical_json_bytes(after)
    )
    (source / "corpus_reaudit_candidates.json").write_bytes(
        reaudit.canonical_json_bytes({
            "schema_version": 1,
            "record_kind": "historical_context_local_corpus_reaudit_candidates",
            "programme_version": "historical-context-local-corpus-reaudit-v1",
            "eligible_quote_count": 1,
            "candidate_count": 1,
            "records": [candidate],
        })
    )
    for path in source.iterdir():
        path.chmod(0o600)

    output = tmp_path / "new-run"
    output.mkdir(mode=0o700)
    output.chmod(0o700)
    monkeypatch.setenv(reaudit.LOCAL_ARCHIVE_ROOT_ENV, str(archive))
    monkeypatch.setattr(reaudit, "resolve_project_root", lambda _path: project.resolve())
    monkeypatch.setattr(
        reaudit, "_registered_worktrees", lambda _project: [project.resolve()]
    )
    monkeypatch.setattr(
        reaudit, "authoritative_hashes", lambda _project: {"inputs": "stable"}
    )
    monkeypatch.setattr(
        reaudit,
        "derive_eligible_targets",
        lambda _project, prepare_search_strategy=False: {"targets": [synthetic]},
    )
    return {
        "project": project,
        "archive": archive,
        "source": source,
        "output": output,
        "document_path": document_path,
        "quotation": quotation,
    }


def update_source_candidate(case: dict, **changes: object) -> None:
    path = case["source"] / "corpus_reaudit_candidates.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["records"][0].update(changes)
    path.write_bytes(reaudit.canonical_json_bytes(document))
    path.chmod(0o600)


def reclassified_candidate(case: dict) -> dict:
    return json.loads(
        (case["output"] / "corpus_reaudit_candidates.json").read_text()
    )["records"][0]


def test_reclassification_accepts_valid_file_passage_and_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepare_reclassification_case(tmp_path, monkeypatch)
    summary = reaudit.reclassify_existing(
        case["project"], case["source"], case["output"]
    )
    candidate_document = json.loads(
        (case["output"] / "corpus_reaudit_candidates.json").read_text()
    )
    assert summary["source_run_inventory_was_unstable"] is True
    assert summary["candidate_level_evidence_identities_revalidated"] is True
    assert summary["candidate_identity_valid_count"] == 1
    assert summary["stale_candidate_count"] == 0
    assert summary["positive_candidates_remaining_valid"] == 1
    assert summary["recorded_positive_candidates_remaining_valid"] == 1
    assert summary["candidate_semantic_reverification_required_count"] == 1
    assert summary["candidate_semantic_reverified_count"] == 1
    assert summary["candidate_semantic_reverification_accepted_count"] == 1
    assert summary["candidate_semantic_reverification_rejected_count"] == 0
    assert summary["reverified_positive_candidate_count"] == 1
    candidate = candidate_document["records"][0]
    assert candidate["candidate_evidence_identity_status"] == "valid"
    assert "exact local file SHA-256 still match" in candidate[
        "candidate_evidence_identity_reason"
    ]
    assert candidate["candidate_semantic_reverification_status"] == (
        "reverified_accepted"
    )
    assert candidate["candidate_document_rematched_without_discovery"] is True
    assert all(
        path.stat().st_mode & 0o777 == 0o600
        for path in case["output"].iterdir()
    )


def test_advisory_selected_old_negative_counts_as_fresh_positive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepare_reclassification_case(tmp_path, monkeypatch)
    update_source_candidate(
        case,
        accepted_as_primary_evidence=False,
        proposed_change_category=[reaudit.CATEGORY_STRONGER_EVIDENCE],
    )
    summary = reaudit.reclassify_existing(
        case["project"], case["source"], case["output"]
    )
    candidate = reclassified_candidate(case)
    assert candidate["recorded_accepted_as_primary_evidence"] is False
    assert candidate["candidate_semantic_reverification_status"] == (
        "reverified_accepted"
    )
    assert summary["recorded_positive_candidates_remaining_valid"] == 0
    assert summary["reverified_positive_candidate_count"] == 1
    assert summary["positive_candidates_remaining_valid"] == 1


def test_reclassification_ignores_altered_recorded_passage_and_rematches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepare_reclassification_case(tmp_path, monkeypatch)
    update_source_candidate(
        case,
        supporting_passage=case["quotation"] + " Altered ledger passage.",
    )
    reaudit.reclassify_existing(case["project"], case["source"], case["output"])
    candidate = reclassified_candidate(case)
    assert candidate["candidate_evidence_stale"] is False
    assert candidate["candidate_evidence_identity_status"] == "valid"
    assert candidate["accepted_as_primary_evidence"] is True
    assert candidate["candidate_semantic_reverification_status"] == "reverified_accepted"
    assert candidate["recorded_semantic_history"]["supporting_passage"].endswith(
        "Altered ledger passage."
    )
    assert reaudit.word_tokens(candidate["supporting_passage"]) == (
        reaudit.word_tokens(case["quotation"])
    )


def test_reclassification_does_not_trust_recorded_passage_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepare_reclassification_case(tmp_path, monkeypatch)
    update_source_candidate(case, supporting_passage_sha256="0" * 64)
    reaudit.reclassify_existing(case["project"], case["source"], case["output"])
    candidate = reclassified_candidate(case)
    assert candidate["candidate_evidence_stale"] is False
    assert candidate["accepted_as_primary_evidence"] is True
    assert candidate["supporting_passage_sha256"] != "0" * 64
    assert candidate["recorded_semantic_history"]["supporting_passage_sha256"] == (
        "0" * 64
    )


def test_reclassification_replaces_absent_recorded_passage_with_fresh_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepare_reclassification_case(tmp_path, monkeypatch)
    absent_passage = "This supporting passage is absent from the document."
    update_source_candidate(
        case,
        supporting_passage=absent_passage,
        supporting_passage_sha256=hashlib.sha256(
            absent_passage.encode("utf-8")
        ).hexdigest(),
    )
    reaudit.reclassify_existing(case["project"], case["source"], case["output"])
    candidate = reclassified_candidate(case)
    assert candidate["candidate_evidence_stale"] is False
    assert candidate["accepted_as_primary_evidence"] is True
    assert reaudit.word_tokens(candidate["supporting_passage"]) == (
        reaudit.word_tokens(case["quotation"])
    )
    assert candidate["recorded_semantic_history"]["supporting_passage"] == (
        absent_passage
    )


def test_identity_valid_recorded_positive_can_be_semantically_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepare_reclassification_case(tmp_path, monkeypatch)
    case["document_path"].write_bytes(
        html("103384", "<p>This document contains unrelated wording only.</p>")
    )
    update_source_candidate(
        case,
        local_file_sha256=reaudit.file_sha256(case["document_path"]),
    )
    summary = reaudit.reclassify_existing(
        case["project"], case["source"], case["output"]
    )
    candidate = reclassified_candidate(case)
    assert candidate["candidate_evidence_identity_status"] == "valid"
    assert candidate["candidate_evidence_stale"] is False
    assert candidate["candidate_semantic_reverification_status"] == (
        "reverified_rejected_no_support"
    )
    assert candidate["accepted_as_primary_evidence"] is False
    assert candidate["match_type"] == "no support"
    assert summary["positive_candidates_remaining_valid"] == 0
    assert summary["candidate_semantic_reverification_rejected_count"] == 1


def test_unselected_candidate_cannot_retain_old_positive_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepare_reclassification_case(tmp_path, monkeypatch)
    update_source_candidate(
        case,
        accepted_as_primary_evidence=False,
        proposed_unblock=False,
        candidate_classification="strong_primary_evidence",
        proposed_change_category=[reaudit.CATEGORY_NO_CHANGE],
    )
    reaudit.reclassify_existing(case["project"], case["source"], case["output"])
    candidate = reclassified_candidate(case)
    assert candidate["candidate_evidence_identity_status"] == (
        "not_selected_for_revalidation"
    )
    assert candidate["candidate_semantic_reverification_status"] == (
        "not_selected_for_reverification"
    )
    assert candidate["accepted_as_primary_evidence"] is False
    assert candidate["proposed_change_category"] == [reaudit.CATEGORY_NO_CHANGE]


@pytest.mark.parametrize("change", ["changed", "missing"])
def test_reclassification_marks_changed_or_missing_candidate_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    case = prepare_reclassification_case(tmp_path, monkeypatch)
    if change == "changed":
        case["document_path"].write_bytes(
            html("103384", f"<p>{case['quotation']} Changed archive body.</p>")
        )
    else:
        case["document_path"].unlink()
    summary = reaudit.reclassify_existing(
        case["project"], case["source"], case["output"]
    )
    candidate_document = json.loads(
        (case["output"] / "corpus_reaudit_candidates.json").read_text()
    )
    candidate = candidate_document["records"][0]
    assert summary["stale_candidate_count"] == 1
    assert summary["positive_candidates_remaining_valid"] == 0
    assert candidate["candidate_evidence_stale"] is True
    assert candidate["accepted_as_primary_evidence"] is False
    assert candidate["proposed_change_category"] == [reaudit.CATEGORY_NO_CHANGE]


def test_reclassification_performs_zero_search_index_discovery_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepare_reclassification_case(tmp_path, monkeypatch)

    def unexpected_discovery(*_args: object, **_kwargs: object) -> dict:
        raise AssertionError("search/index discovery must not run")

    monkeypatch.setattr(LocalMTFDocumentIndex, "discover", unexpected_discovery)
    summary = reaudit.reclassify_existing(
        case["project"], case["source"], case["output"]
    )
    assert summary["search_index_discovery_call_count"] == 0
    assert summary["archive_inventory_rescan_performed"] is False


def test_reclassification_does_not_scan_archive_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepare_reclassification_case(tmp_path, monkeypatch)

    def unexpected_inventory(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("archive inventory must not be scanned")

    monkeypatch.setattr(LocalArchiveMirror, "inventory", unexpected_inventory)
    summary = reaudit.reclassify_existing(
        case["project"], case["source"], case["output"]
    )
    assert summary["archive_inventory_rescan_performed"] is False


def test_reclassification_leaves_original_private_package_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepare_reclassification_case(tmp_path, monkeypatch)
    before = reaudit._source_run_snapshot(case["source"])
    reaudit.reclassify_existing(case["project"], case["source"], case["output"])
    assert reaudit._source_run_snapshot(case["source"]) == before


def test_source_mutation_is_detected_before_any_output_is_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepare_reclassification_case(tmp_path, monkeypatch)
    source_contents = {
        path.name: path.read_bytes() for path in case["source"].iterdir()
    }
    candidate_path = case["source"] / "corpus_reaudit_candidates.json"
    injected_contents = source_contents[candidate_path.name] + b"\n"
    original_reclassify = reaudit._reclassify_candidate

    def mutate_source_during_reclassification(*args: object, **kwargs: object) -> dict:
        result = original_reclassify(*args, **kwargs)
        candidate_path.write_bytes(injected_contents)
        candidate_path.chmod(0o600)
        return result

    monkeypatch.setattr(
        reaudit, "_reclassify_candidate", mutate_source_during_reclassification
    )
    with pytest.raises(reaudit.ReauditError, match="source run changed"):
        reaudit.reclassify_existing(
            case["project"], case["source"], case["output"]
        )

    assert candidate_path.read_bytes() == injected_contents
    assert all(
        path.read_bytes() == source_contents[path.name]
        for path in case["source"].iterdir()
        if path != candidate_path
    )
    assert not any(case["output"].iterdir())


def test_output_write_failure_removes_entire_incomplete_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepare_reclassification_case(tmp_path, monkeypatch)
    original_write_json = reaudit.write_json

    def fail_after_write(
        run_dir: Path, filename: str, value: object
    ) -> None:
        original_write_json(run_dir, filename, value)
        if filename == "blocked_quote_reassessment.json":
            raise OSError("injected output-write failure")

    monkeypatch.setattr(reaudit, "write_json", fail_after_write)
    with pytest.raises(OSError, match="injected output-write failure"):
        reaudit.reclassify_existing(
            case["project"], case["source"], case["output"]
        )
    assert not any(case["output"].iterdir())


def test_reclassification_outputs_do_not_disclose_private_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepare_reclassification_case(tmp_path, monkeypatch)
    reaudit.reclassify_existing(case["project"], case["source"], case["output"])
    encoded = "\n".join(
        path.read_text(encoding="utf-8") for path in case["output"].iterdir()
    )
    assert str(case["archive"]) not in encoded
    assert str(case["source"]) not in encoded
    assert str(case["output"]) not in encoded

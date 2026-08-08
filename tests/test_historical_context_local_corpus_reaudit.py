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
        "known_mtf_document_ids": [document_id] if document_id else [],
        "direct_mtf_public_urls": (
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
        "known_mtf_document_ids": ["100000"],
        "direct_mtf_public_urls": [
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


def test_reclassification_accepts_unchanged_candidate_file_hash(
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
    assert candidate_document["records"][0]["candidate_evidence_identity_status"] == "valid"
    assert all(
        path.stat().st_mode & 0o777 == 0o600
        for path in case["output"].iterdir()
    )


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


def test_reclassification_leaves_original_private_package_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = prepare_reclassification_case(tmp_path, monkeypatch)
    before = reaudit._source_run_snapshot(case["source"])
    reaudit.reclassify_existing(case["project"], case["source"], case["output"])
    assert reaudit._source_run_snapshot(case["source"]) == before


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

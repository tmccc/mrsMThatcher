from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from jsonschema import validate as validate_json_schema
import historical_context_formatter as context_module

from historical_context_formatter import (
    AmbiguousContextReplyOutcome,
    HistoricalContextReplyStore,
    VERIFICATION_LABELS,
    format_context_reply,
    format_context_reply_public,
    format_context_reply_v2,
    load_and_validate_corpus,
    packet_is_attributed_to_margaret_thatcher,
    packet_for_posted_quote,
    quote_text_hash,
    select_primary_source,
    x_weighted_length,
)
from transaction_mutation_authority import issue_transaction_mutation_authority

RESEARCH = Path("semantic_alignment_research/quote_research_full_001")


def _test_mutation_authority(operation: str):
    return issue_transaction_mutation_authority(
        lambda _verified_operation: None,
        operation=operation,
    )


def _historical_store(*args, **kwargs):
    kwargs.setdefault(
        "mutation_authority_provider",
        _test_mutation_authority,
    )
    return HistoricalContextReplyStore(*args, **kwargs)


@pytest.fixture(scope="module")
def corpus():
    return load_and_validate_corpus(RESEARCH)


def test_runtime_context_reply_state_files_are_gitignored():
    patterns = set(Path(".gitignore").read_text().splitlines())
    assert "historical_context_reply_history.json" in patterns
    assert "historical_context_reply_receipt.json" in patterns


def test_completed_corpus_validation_and_unresolved_rejection(corpus):
    packets, unresolved = corpus
    assert len(packets) == 627 and len(unresolved) == 5 and not set(packets) & unresolved
    assert packet_for_posted_quote(packets, unresolved, next(iter(unresolved)), "anything") is None


def test_posted_quote_lookup_rejects_attribution_ineligible_completed_packet(corpus):
    packets, unresolved = corpus
    quote_id = "7f75c4d086fb67b0e54d9d63dbe470dc6f9f929aee00ce4a02d01bbc9c8d4646"
    packet = packets[quote_id]

    assert packet_for_posted_quote(packets, unresolved, quote_id, packet["quote_text"]) is None


def test_bare_maiden_name_is_not_a_thatcher_identity_attestation():
    assert packet_is_attributed_to_margaret_thatcher({
        "speaker": "Margaret Roberts",
        "verification_status": "exact",
    }) is False
    assert packet_is_attributed_to_margaret_thatcher({
        "speaker": "Margaret Thatcher (as Margaret Roberts)",
        "verification_status": "exact",
    }) is True


def test_duplicate_manifest_record_is_rejected_before_dictionary_collapse(tmp_path):
    research = tmp_path / "research"
    (research / "final_unresolved").mkdir(parents=True)
    shutil.copy(RESEARCH / "research_packets.json", research / "research_packets.json")
    shutil.copy(
        RESEARCH / "final_unresolved" / "final_research_status.json",
        research / "final_unresolved" / "final_research_status.json",
    )
    manifest = json.loads((RESEARCH / "corpus_manifest.json").read_text())
    manifest["records"].append(dict(manifest["records"][0]))
    (research / "corpus_manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(RuntimeError, match="declared unique record count"):
        load_and_validate_corpus(research)


def test_duplicate_unresolved_status_id_is_rejected_before_set_collapse(tmp_path):
    research = tmp_path / "research"
    (research / "final_unresolved").mkdir(parents=True)
    shutil.copy(RESEARCH / "research_packets.json", research / "research_packets.json")
    shutil.copy(RESEARCH / "corpus_manifest.json", research / "corpus_manifest.json")
    status = json.loads((RESEARCH / "final_unresolved" / "final_research_status.json").read_text())
    status["unresolved_quote_ids"].append(status["unresolved_quote_ids"][0])
    (research / "final_unresolved" / "final_research_status.json").write_text(json.dumps(status))

    with pytest.raises(RuntimeError, match="status counts or unresolved quote IDs"):
        load_and_validate_corpus(research)


def test_corpus_partition_counts_are_derived_from_signed_metadata(tmp_path, corpus):
    packets, unresolved = corpus
    completed_id = next(
        quote_id
        for quote_id, packet in packets.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    )
    unresolved_id = next(iter(unresolved))
    original_manifest = json.loads((RESEARCH / "corpus_manifest.json").read_text())
    records_by_id = {
        record["quote_id"]: record
        for record in original_manifest["records"]
    }

    research = tmp_path / "research"
    (research / "final_unresolved").mkdir(parents=True)
    packet_document = {
        "schema_version": 1,
        "items": {completed_id: packets[completed_id]},
    }
    packet_bytes = (json.dumps(packet_document, indent=2, sort_keys=True) + "\n").encode()
    (research / "research_packets.json").write_bytes(packet_bytes)
    (research / "corpus_manifest.json").write_text(json.dumps({
        **{key: value for key, value in original_manifest.items() if key != "records"},
        "record_count": 2,
        "records": [records_by_id[completed_id], records_by_id[unresolved_id]],
    }))
    original_status = json.loads(
        (RESEARCH / "final_unresolved" / "final_research_status.json").read_text()
    )
    (research / "final_unresolved" / "final_research_status.json").write_text(
        json.dumps({
            **original_status,
            "completed_quotes": 1,
            "total_manifest_quotes": 2,
            "unresolved_quotes": 1,
            "unresolved_quote_ids": [unresolved_id],
            "corpus_hash": hashlib.sha256(packet_bytes).hexdigest(),
        })
    )

    loaded_packets, loaded_unresolved = load_and_validate_corpus(research)

    assert set(loaded_packets) == {completed_id}
    assert loaded_unresolved == {unresolved_id}


def test_exact_deterministic_formatting_and_character_limit(corpus):
    packets, _ = corpus; packet = next(row for row in packets.values() if row["verification_status"] == "exact")
    first = format_context_reply(packet); second = format_context_reply(packet)
    assert first == second and first["verification_label"] == "Exact wording"
    assert first["text"].startswith("Historical context\n") and "\n\nMeaning: " in first["text"]
    assert "\n\nVerification: Exact wording\nSource: " in first["text"]
    assert "\n\nSource: " not in first["text"]
    assert first["character_count"] == x_weighted_length(first["text"]) <= 4000


def test_formatter_output_matches_published_schema_with_optional_sections(corpus):
    schema = json.loads(Path("historical_context_reply_schema.json").read_text())
    packets, _ = corpus
    packet = next(row for row in packets.values() if row["verification_status"] == "exact")

    validate_json_schema(format_context_reply(packet), schema)
    validate_json_schema(format_context_reply(
        packet,
        include_meaning=False,
        include_source=False,
        include_verification=False,
    ), schema)
    current = format_context_reply_v2(packet)
    validate_json_schema(current, schema)
    assert current["formatter_version"] == context_module.HISTORICAL_CONTEXT_FORMATTER_V5


def test_formatter_rejects_unknown_rendering_mode(corpus):
    packet = next(iter(corpus[0].values()))

    with pytest.raises(ValueError, match="rendering_mode must be public or internal"):
        format_context_reply_v2(packet, rendering_mode="x")


@pytest.mark.parametrize(
    ("supported_fields", "expected_context"),
    [
        (
            [],
            "Context — The surviving attribution does not establish an "
            "occasion, date or immediate historical issue.",
        ),
        (["source_event"], "Context — Synthetic conference speech."),
        (
            ["date"],
            "Context — The surviving record dates this wording to 20 May "
            "1981, but does not establish its occasion.",
        ),
        (
            ["source_event", "date"],
            "Context — Synthetic conference speech, 20 May 1981.",
        ),
    ],
)
def test_public_formatter_obeys_all_event_date_admission_combinations(
    corpus, supported_fields, expected_context
):
    packet = copy.deepcopy(next(iter(corpus[0].values())))
    packet["source_event"] = "Synthetic conference speech"
    packet["date"] = "1981-05-20"
    packet["_source_role_audit"]["public_context_supported_fields"] = (
        supported_fields
    )

    rendered = format_context_reply_public(
        packet,
        include_meaning=False,
        include_source=False,
        include_verification=False,
    )

    assert rendered is not None
    assert rendered["text"] == expected_context


def test_public_formatter_omits_only_the_generic_context_section(corpus):
    packets, _ = corpus
    packet = next(
        row
        for row in packets.values()
        if not row["_source_role_audit"]["public_context_supported_fields"]
        and (
            rendered := format_context_reply_public(row)
        ) is not None
        and rendered["meaning_included"]
    )

    public = format_context_reply_public(packet)
    internal = format_context_reply_v2(
        packet,
        rendering_mode="internal",
    )

    assert public is not None
    assert public["text"].startswith("Meaning — ")
    assert "Context —" not in public["text"]
    assert public["template_variant"] == "compact_generic_context_omitted"
    assert internal is not None
    assert internal["text"].startswith(
        "Context — The surviving attribution does not establish an occasion, "
        "date or immediate historical issue."
    )


def test_public_formatter_retains_substantive_context_section(corpus):
    packet = copy.deepcopy(next(iter(corpus[0].values())))
    packet["immediate_subject"] = "A substantive historical issue."
    packet["historical_context"] = "A substantive historical issue."
    packet["_source_role_audit"]["public_context_supported_fields"] = [
        "historical_context"
    ]

    rendered = format_context_reply_public(packet)

    assert rendered is not None
    assert rendered["text"].startswith(
        "Context — A substantive historical issue."
    )
    assert rendered["template_variant"] != "compact_generic_context_omitted"


@pytest.mark.parametrize(
    "source_event",
    [
        "Interview recorded in 1984",
        "First statement / later recollection",
    ],
)
def test_public_event_only_context_rejects_date_leakage_and_diagnostic_slash(
    corpus, source_event
):
    packet = copy.deepcopy(next(iter(corpus[0].values())))
    packet["source_event"] = source_event
    packet["date"] = "1984"
    packet["_source_role_audit"]["public_context_supported_fields"] = [
        "source_event"
    ]

    rendered = format_context_reply_public(
        packet,
        include_meaning=False,
        include_source=False,
        include_verification=False,
    )

    assert rendered is not None
    assert rendered["text"] == (
        "Context — The surviving record identifies an occasion, but does not "
        "establish a reliable date."
    )


def test_public_b32_context_uses_reviewed_date_without_diagnostic_slash(
    corpus,
):
    quote_id = "b32d8cdf5977dee436857e8060d3a83ebfe54de9f6dabb20ffc65a0796338b5c"
    packet = corpus[0][quote_id]

    assert packet["_source_role_audit"]["public_context_supported_fields"] == [
        "source_event",
        "date",
        "historical_context",
    ]
    public = format_context_reply_public(
        packet,
        include_meaning=False,
        include_source=False,
        include_verification=False,
    )
    internal = format_context_reply_v2(
        packet,
        include_meaning=False,
        include_source=False,
        include_verification=False,
        rendering_mode="internal",
    )

    assert public is not None
    assert public["text"] == (
        "Context — TV Interview for BBC1 Panorama, 9 April 1984: Thatcher was "
        "preparing to take office in 1979, a time when the UK was plagued by "
        "economic stagnation and industrial unrest."
    )
    assert " / " not in public["text"]
    assert internal is not None
    assert internal["text"].startswith(public["text"])


def test_standalone_formatter_cli_distinguishes_internal_and_public_rendering(corpus, capsys):
    packet = corpus[0][
        "0056972ab9debcb840c36ac23ad0387e715cb22fc4ed49dbcf35159f83592aa5"
    ]
    assert context_module.main([
        "--research-dir", str(RESEARCH), "--quote-id", packet["quote_id"],
    ]) == 0
    output = capsys.readouterr().out
    assert output.startswith("Context — ")
    assert "Historical context\n" not in output
    assert "Confidence — Attribution:" in output

    assert context_module.main([
        "--research-dir", str(RESEARCH), "--quote-id", packet["quote_id"],
        "--rendering-mode", "public",
    ]) == 0
    public_output = capsys.readouterr().out
    assert public_output.startswith("Meaning — ")
    assert (
        "Context — The surviving attribution does not establish an occasion, "
        "date or immediate historical issue."
    ) not in public_output
    assert "Confidence —" not in public_output


def test_standalone_formatter_cli_rejects_non_thatcher_quote_id(corpus, capsys):
    packets, _ = corpus
    packet = next(
        row for row in packets.values()
        if not packet_is_attributed_to_margaret_thatcher(row)
    )

    with pytest.raises(SystemExit, match="no attribution-eligible completed canonical research packet"):
        context_module.main([
            "--research-dir", str(RESEARCH), "--quote-id", packet["quote_id"],
        ])

    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("status,label", sorted(VERIFICATION_LABELS.items()))
def test_verification_wording_is_explicit(corpus, status, label):
    packets, _ = corpus; packet = next(row for row in packets.values() if row["verification_status"] == status)
    assert format_context_reply(packet)["verification_label"] == label


@pytest.mark.parametrize("status", ["paraphrase", "composite", "misattributed", "unverified"])
def test_uncertain_wording_does_not_claim_it_was_spoken_during_source_event(corpus, status):
    packets, _ = corpus
    packet = next(row for row in packets.values() if row["verification_status"] == status)
    result = format_context_reply(packet)
    assert "Occasion:" not in result["text"]
    assert "Source event:" in result["text"]


def test_source_selection_priority_and_aggregator_restriction():
    packet = {"verification_status": "exact", "stable_locator": "Archive shelf A", "sources": [
        {"title": "General newspaper", "url": "https://news.example/a", "source_type": "secondary"},
        {"title": "Hansard debate", "url": "https://hansard.parliament.uk/a", "source_type": "official"},
        {"title": "AZQuotes", "url": "https://azquotes.com/a", "source_type": "aggregation"},
    ]}
    assert "hansard" in select_primary_source(packet)["url"]
    packet["sources"] = packet["sources"][2:]
    assert select_primary_source(packet) == {"title": "Archive shelf A", "url": "", "source_type": "canonical_stable_locator"}


def test_authoritative_stable_locator_beats_secondary_web_source():
    packet = {
        "verification_status": "exact",
        "stable_locator": "Margaret Thatcher Foundation Archive, Document 123456",
        "sources": [{
            "title": "Retrospective newspaper commentary",
            "url": "https://news.example/retrospective",
            "source_type": "secondary",
        }],
    }
    assert select_primary_source(packet) == {
        "title": "Margaret Thatcher Foundation Archive, Document 123456",
        "url": "",
        "source_type": "canonical_stable_locator",
    }


def test_grounding_redirect_is_never_published_when_stable_locator_exists():
    packet = {
        "verification_status": "exact",
        "stable_locator": "Margaret Thatcher Foundation Archive, November 8, 1993",
        "sources": [{
            "title": "margaretthatcher.org",
            "url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/opaque-token",
            "source_type": "grounded_web_source",
        }],
    }
    assert select_primary_source(packet) == {
        "title": "Margaret Thatcher Foundation Archive, November 8, 1993",
        "url": "",
        "source_type": "canonical_stable_locator",
    }


def test_context_reply_uses_compact_archive_entry_layout(corpus):
    packets, _ = corpus
    packet = packets["e28d24c49780a4d8a0c248097ee4962f941fdf1b2ec687a1bf52cddabc95995b"]

    text = format_context_reply(packet)["text"]

    assert text.startswith(
        'Historical context\nOccasion: Speech to the Fraser Institute '
        '("The New World Order").\n'
    )
    assert "\n\nMeaning: Capitalism is inherently moral" in text
    assert "\nVerification: Exact wording\n" in text
    assert (
        "\nSource: Margaret Thatcher Foundation, Speech to the Fraser "
        'Institute ("The New World Order"), 1993-11-08 (Document 108325)'
    ) in text
    assert "\nhttps://www.margaretthatcher.org/document/108325" in text
    assert "vertexaisearch.cloud.google.com" not in text
    assert "\nSource\n" not in text


def test_aggregator_is_rejected_when_only_url_reveals_it():
    packet = {
        "verification_status": "exact",
        "stable_locator": "Canonical archive reference",
        "sources": [{
            "title": "Margaret Thatcher quotation",
            "url": "https://www.azquotes.com/quote/123",
            "source_type": "web",
        }],
    }
    assert select_primary_source(packet) == {
        "title": "Canonical archive reference",
        "url": "",
        "source_type": "canonical_stable_locator",
    }


def test_unverified_wisesayings_source_is_labelled_as_attribution_evidence():
    packet = {
        "verification_status": "unverified",
        "stable_locator": "unknown",
        "sources": [{
            "title": "wisesayings.com",
            "url": "https://example.test/grounding-redirect",
            "source_type": "grounded_web_source",
        }],
    }
    assert select_primary_source(packet) == {
        "title": "Attribution record: wisesayings.com",
        "url": "https://example.test/grounding-redirect",
        "source_type": "attribution_error_documentation",
    }


@pytest.mark.parametrize("placeholder", ["N/A", "None", "not available", "unknown"])
def test_placeholder_stable_locator_is_not_published_as_source(placeholder):
    packet = {
        "verification_status": "exact",
        "stable_locator": placeholder,
        "sources": [],
    }
    assert select_primary_source(packet) is None


def test_placeholder_context_metadata_is_not_rendered_literally():
    packet = {
        "quote_id": "a" * 64,
        "verification_status": "exact",
        "source_event": "N/A",
        "date": "None",
        "immediate_subject": "not available",
        "historical_context": "unavailable",
        "intended_argument": "A supported meaning",
        "literal_meaning": "",
        "stable_locator": "Archive reference",
        "sources": [],
    }
    result = format_context_reply(packet)
    assert "Occasion: Source event not established." in result["text"]
    assert "Date: Unknown" in result["text"]
    assert "Immediate context:" not in result["text"]
    assert "N/A" not in result["text"] and "None" not in result["text"]


def test_shortening_removes_meaning_before_provenance(corpus):
    packets, _ = corpus
    packet = max(packets.values(), key=lambda row: len(row["intended_argument"]))
    result = format_context_reply(packet, maximum_length=180)
    assert result and result["character_count"] <= 180
    assert "Historical context\n" in result["text"] and "Verification:" in result["text"] and "Source:" in result["text"]


def test_all_completed_packets_format_without_hashtags_or_emoji(corpus):
    packets, _ = corpus
    results = [format_context_reply(packet) for packet in packets.values()]
    assert all(results) and all(result["character_count"] <= 4000 for result in results)
    assert all(" #" not in result["text"] and "😀" not in result["text"] for result in results)
    assert all("vertexaisearch.cloud.google.com" not in result["text"] for result in results)


def test_normalised_text_lookup_preserves_canonical_identity(corpus):
    packets, unresolved = corpus
    packet = next(row for row in packets.values() if "  " in row["quote_text"])
    posted = " ".join(packet["quote_text"].split())
    found = packet_for_posted_quote(packets, unresolved, quote_text_hash(posted), posted)
    assert found["quote_id"] == packet["quote_id"] and found["quote_text"] == packet["quote_text"]


def test_transactional_receipt_resume_and_duplicate_prevention(tmp_path):
    store = _historical_store(tmp_path / "history.json", tmp_path / "receipt.json")
    calls = []
    create = lambda **kwargs: calls.append(kwargs) or {"data": {"id": "222"}}
    result = store.post(parent_post_id="111", quote_id="a" * 64, reply_text="Context", create_post=create, now_epoch=lambda: 123)
    assert result["status"] == "completed" and not store.receipt_path.exists()
    again = store.post(parent_post_id="111", quote_id="a" * 64, reply_text="Context", create_post=create, now_epoch=lambda: 124)
    assert again["status"] == "already_completed" and len(calls) == 1
    assert json.loads(store.history_path.read_text())["items"]["111"]["reply_post_id"] == "222"


def test_completed_parent_with_different_quote_identity_is_conflict(tmp_path):
    store = _historical_store(tmp_path / "history.json", tmp_path / "receipt.json")
    store.post(
        parent_post_id="111",
        quote_id="a" * 64,
        reply_text="Context",
        create_post=lambda **kwargs: {"data": {"id": "222"}},
        now_epoch=lambda: 123,
    )

    with pytest.raises(RuntimeError, match="quote identity conflicts"):
        store.post(
            parent_post_id="111",
            quote_id="b" * 64,
            reply_text="Different context",
            create_post=lambda **kwargs: pytest.fail("identity conflict must not post"),
            now_epoch=lambda: 124,
        )


def test_epoch_is_resolved_before_remote_post(tmp_path):
    store = _historical_store(tmp_path / "history.json", tmp_path / "receipt.json")

    with pytest.raises(RuntimeError, match="clock unavailable"):
        store.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=lambda **kwargs: pytest.fail("clock failure must occur before remote post"),
            now_epoch=lambda: (_ for _ in ()).throw(RuntimeError("clock unavailable")),
        )


@pytest.mark.parametrize("bad_epoch", [1_499_999_999, 4_102_444_801])
def test_production_transport_rejects_out_of_range_epoch_before_remote_post(
    tmp_path,
    bad_epoch,
):
    store = _historical_store(
        tmp_path / "history.json",
        tmp_path / "receipt.json",
    )

    with pytest.raises(RuntimeError, match="outside the supported"):
        store.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=lambda **kwargs: pytest.fail(
                "invalid production clock must fail before remote post"
            ),
            now_epoch=lambda: bad_epoch,
            require_confirmed_transport=True,
        )

    assert not store.receipt_path.exists()


def test_malformed_response_data_is_ambiguous_and_preserves_receipt(tmp_path):
    store = _historical_store(
        tmp_path / "history.json",
        tmp_path / "receipt.json",
    )

    with pytest.raises(AmbiguousContextReplyOutcome, match="no valid post identity"):
        store.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=lambda **kwargs: {"data": ["not", "an", "object"]},
            now_epoch=lambda: 123,
        )

    assert json.loads(store.receipt_path.read_text())["lifecycle_state"] == "sending"
    assert not store.history_path.exists()


def test_overlong_numeric_response_id_remains_ambiguous_and_preserves_receipt(tmp_path):
    store = _historical_store(tmp_path / "history.json", tmp_path / "receipt.json")
    with pytest.raises(AmbiguousContextReplyOutcome, match="may have been accepted"):
        store.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=lambda **kwargs: {"data": {"id": "2" * 31}},
            now_epoch=lambda: 123,
        )
    assert json.loads(store.receipt_path.read_text())["lifecycle_state"] == "sending"
    assert not store.history_path.exists()


def test_post_transmission_rate_limit_is_ambiguous_and_preserves_receipt(
    tmp_path,
):
    store = _historical_store(
        tmp_path / "history.json",
        tmp_path / "receipt.json",
    )

    class RateLimitError(RuntimeError):
        service = "x"
        status_code = 429
        reset_epoch = 9_500

    with pytest.raises(AmbiguousContextReplyOutcome, match="not proved"):
        store.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=lambda **_kwargs: (_ for _ in ()).throw(
                RateLimitError("rate limited")
            ),
            now_epoch=lambda: 123,
        )
    assert json.loads(store.receipt_path.read_text())["lifecycle_state"] == "sending"
    assert not store.history_path.exists()


def test_long_reply_is_sent_unchanged_through_existing_post_path(tmp_path):
    store = _historical_store(tmp_path / "history.json", tmp_path / "receipt.json")
    reply = "Context\n" + ("Historically grounded context. " * 20)
    assert len(reply) > 280
    calls = []
    result = store.post(
        parent_post_id="111",
        quote_id="a" * 64,
        reply_text=reply,
        create_post=lambda **kwargs: calls.append(kwargs) or {"data": {"id": "222"}},
        now_epoch=lambda: 123,
    )
    assert result["status"] == "completed"
    prepared = calls[0].pop("prepared_historical_context_reply_receipt")
    assert (
        prepared["lifecycle_state"],
        prepared["parent_post_id"],
        prepared["reply_text"],
    ) == ("sending", "111", reply)
    assert calls == [{
        "text": reply,
        "media_ids": None,
        "reply_to_id": "111",
        "made_with_ai": False,
    }]


def test_formatter_v5_metadata_is_durable_and_prevents_duplicate_after_restart(
    tmp_path, corpus
):
    packet = next(iter(corpus[0].values()))
    formatted = format_context_reply_v2(packet)
    assert formatted is not None
    assert formatted["formatter_version"] == context_module.HISTORICAL_CONTEXT_FORMATTER_V5
    metadata = {
        "formatter_version": formatted["formatter_version"],
        "template_variant": formatted["template_variant"],
        "meaning_included": formatted["meaning_included"],
        "meaning_decision_reason": formatted["meaning_decision_reason"],
        "raw_character_count": formatted["raw_character_count"],
        "weighted_character_count": formatted["weighted_character_count"],
        "verification_label": formatted["verification_label"],
        "source_class": formatted["source_class"],
        "historical_confidence": formatted["historical_confidence"],
        "shortening_applied": formatted["shortening_applied"],
        "confidence_dimensions": formatted["confidence_dimensions"],
        "source_role_audit_version": formatted["source_role_audit_version"],
        "rendering_mode": formatted["rendering_mode"],
    }
    calls = []
    store = _historical_store(tmp_path / "history.json", tmp_path / "receipt.json")
    result = store.post(
        parent_post_id="111",
        quote_id=packet["quote_id"],
        reply_text=formatted["text"],
        formatter_metadata=metadata,
        create_post=lambda **kwargs: calls.append(kwargs) or {"data": {"id": "222"}},
        now_epoch=lambda: 123,
    )
    assert result["status"] == "completed"
    assert store.history()["items"]["111"]["formatter_metadata"] == metadata

    restarted = _historical_store(store.history_path, store.receipt_path)
    duplicate = restarted.post(
        parent_post_id="111",
        quote_id=packet["quote_id"],
        reply_text=formatted["text"],
        formatter_metadata=metadata,
        create_post=lambda **kwargs: pytest.fail("completed v5 reply must not be duplicated"),
        now_epoch=lambda: 124,
    )
    assert duplicate["status"] == "already_completed"
    assert len(calls) == 1


def test_deployed_v8_metadata_is_durable_and_accepted_under_v9(
    tmp_path,
    corpus,
    monkeypatch,
):
    import historical_context_source_roles

    monkeypatch.setattr(
        historical_context_source_roles,
        "POLICY_VERSION",
        "historical-context-source-roles-v9-archive-provenance",
    )
    packet = next(iter(corpus[0].values()))
    formatted = format_context_reply_v2(packet)
    assert formatted is not None
    metadata = {
        key: formatted[key]
        for key in (
            "formatter_version", "template_variant", "meaning_included",
            "meaning_decision_reason", "raw_character_count",
            "weighted_character_count", "verification_label", "source_class",
            "historical_confidence", "shortening_applied",
            "confidence_dimensions", "source_role_audit_version", "rendering_mode",
        )
    }
    metadata["source_role_audit_version"] = (
        "historical-context-source-roles-v8-claim-specific-public-context"
    )
    assert HistoricalContextReplyStore._valid_formatter_metadata(metadata) is True

    store = _historical_store(
        tmp_path / "history.json", tmp_path / "receipt.json"
    )
    result = store.post(
        parent_post_id="111",
        quote_id=packet["quote_id"],
        reply_text=formatted["text"],
        formatter_metadata=metadata,
        create_post=lambda **kwargs: {"data": {"id": "222"}},
        now_epoch=lambda: 123,
    )

    assert result["status"] == "completed"
    restarted = _historical_store(store.history_path, store.receipt_path)
    assert restarted.history()["items"]["111"]["formatter_metadata"] == metadata


def test_legacy_v2_formatter_metadata_remains_valid_for_existing_receipts():
    metadata = {
        "formatter_version": "historical_context_reply_schema_v2",
        "template_variant": "compact_without_redundant_meaning",
        "meaning_included": False,
        "meaning_decision_reason": "The quotation is already self-contained.",
        "raw_character_count": 240,
        "weighted_character_count": 240,
        "verification_label": "Exact wording",
        "source_class": "primary_archive",
        "historical_confidence": "high",
        "shortening_applied": False,
    }

    assert HistoricalContextReplyStore._valid_formatter_metadata(metadata) is True


def test_legacy_v3_formatter_metadata_remains_valid_for_existing_receipts(corpus):
    formatted = format_context_reply_v2(next(iter(corpus[0].values())))
    metadata = {
        key: formatted[key]
        for key in (
            "template_variant", "meaning_included", "meaning_decision_reason",
            "raw_character_count", "weighted_character_count", "verification_label",
            "source_class", "historical_confidence", "shortening_applied",
            "confidence_dimensions", "source_role_audit_version",
        )
    }
    metadata["formatter_version"] = context_module.HISTORICAL_CONTEXT_FORMATTER_V3
    metadata["source_role_audit_version"] = (
        "historical-context-source-roles-v5-independent-review-and-exclusive-counts"
    )

    assert HistoricalContextReplyStore._valid_formatter_metadata(metadata) is True


def test_legacy_v4_formatter_metadata_remains_valid_for_existing_receipts(corpus):
    formatted = format_context_reply_v2(next(iter(corpus[0].values())))
    metadata = {
        key: formatted[key]
        for key in (
            "formatter_version", "template_variant", "meaning_included",
            "meaning_decision_reason", "raw_character_count",
            "weighted_character_count", "verification_label", "source_class",
            "historical_confidence", "shortening_applied",
            "confidence_dimensions", "source_role_audit_version", "rendering_mode",
        )
    }
    metadata["formatter_version"] = context_module.HISTORICAL_CONTEXT_FORMATTER_V4
    metadata["source_role_audit_version"] = (
        "historical-context-source-roles-v5-independent-review-and-exclusive-counts"
    )

    assert HistoricalContextReplyStore._valid_formatter_metadata(metadata) is True


@pytest.mark.parametrize(
    ("formatter_version", "include_rendering_mode"),
    [
        (context_module.HISTORICAL_CONTEXT_FORMATTER_V3, False),
        (context_module.HISTORICAL_CONTEXT_FORMATTER_V4, True),
    ],
)
def test_production_shaped_legacy_history_accepts_exact_v5_source_role_policy(
    tmp_path, corpus, formatter_version, include_rendering_mode
):
    formatted = format_context_reply_v2(next(iter(corpus[0].values())))
    metadata_keys = {
        "formatter_version", "template_variant", "meaning_included",
        "meaning_decision_reason", "raw_character_count",
        "weighted_character_count", "verification_label", "source_class",
        "historical_confidence", "shortening_applied",
        "confidence_dimensions", "source_role_audit_version",
    }
    if include_rendering_mode:
        metadata_keys.add("rendering_mode")
    metadata = {key: formatted[key] for key in metadata_keys}
    metadata["formatter_version"] = formatter_version
    metadata["source_role_audit_version"] = (
        "historical-context-source-roles-v5-independent-review-and-exclusive-counts"
    )
    item = {
        "schema_version": 1,
        "lifecycle_state": "confirmed",
        "parent_post_id": "111",
        "reply_post_id": "222",
        "quote_id": formatted["quote_id"],
        "reply_text": formatted["text"],
        "reply_epoch": 123,
        "started_at": "2026-07-21T00:00:00Z",
        "attempt_number": 1,
        "confirmed_at": "2026-07-21T00:00:01Z",
        "formatter_metadata": metadata,
        "status": "completed",
    }
    history_path = tmp_path / "history.json"
    history_path.write_text(
        json.dumps({"schema_version": 1, "items": {"111": item}}),
        encoding="utf-8",
    )

    loaded = _historical_store(
        history_path, tmp_path / "receipt.json"
    ).history()

    assert loaded["items"]["111"]["formatter_metadata"] == metadata


@pytest.mark.parametrize(
    ("formatter_version", "include_rendering_mode"),
    [
        (context_module.HISTORICAL_CONTEXT_FORMATTER_V3, False),
        (context_module.HISTORICAL_CONTEXT_FORMATTER_V4, True),
    ],
)
def test_legacy_v3_v4_formatter_metadata_still_rejects_unknown_source_role_policy(
    corpus, formatter_version, include_rendering_mode
):
    formatted = format_context_reply_v2(next(iter(corpus[0].values())))
    metadata_keys = {
        "formatter_version", "template_variant", "meaning_included",
        "meaning_decision_reason", "raw_character_count",
        "weighted_character_count", "verification_label", "source_class",
        "historical_confidence", "shortening_applied",
        "confidence_dimensions", "source_role_audit_version",
    }
    if include_rendering_mode:
        metadata_keys.add("rendering_mode")
    metadata = {key: formatted[key] for key in metadata_keys}
    metadata["formatter_version"] = formatter_version
    metadata["source_role_audit_version"] = (
        "historical-context-source-roles-v5-unrecognised"
    )

    assert HistoricalContextReplyStore._valid_formatter_metadata(metadata) is False


@pytest.mark.parametrize(
    "source_role_audit_version",
    [
        "historical-context-source-roles-v8-unrecognised",
        "historical-context-source-roles-v10-future",
        "historical-context-source-roles-v999",
    ],
)
def test_v5_formatter_metadata_rejects_unknown_or_future_source_role_policy(
    tmp_path, corpus, source_role_audit_version
):
    formatted = format_context_reply_v2(next(iter(corpus[0].values())))
    metadata = {
        key: formatted[key]
        for key in (
            "formatter_version", "template_variant", "meaning_included",
            "meaning_decision_reason", "raw_character_count",
            "weighted_character_count", "verification_label", "source_class",
            "historical_confidence", "shortening_applied",
            "confidence_dimensions", "source_role_audit_version", "rendering_mode",
        )
    }
    metadata["source_role_audit_version"] = source_role_audit_version

    assert HistoricalContextReplyStore._valid_formatter_metadata(metadata) is False
    history_path = tmp_path / "history.json"
    history_path.write_text(json.dumps({
        "schema_version": 1,
        "items": {
            "111": {
                "status": "failed",
                "parent_post_id": "111",
                "quote_id": formatted["quote_id"],
                "reply_text": formatted["text"],
                "attempt_count": 1,
                "failure": "test failure",
                "formatter_metadata": metadata,
            },
        },
    }))
    store = _historical_store(history_path, tmp_path / "receipt.json")

    with pytest.raises(RuntimeError, match="invalid failed context reply history"):
        store.history()


def test_v4_formatter_metadata_accepts_previous_source_role_policy(corpus):
    formatted = format_context_reply_v2(next(iter(corpus[0].values())))
    metadata = {
        key: formatted[key]
        for key in (
            "formatter_version", "template_variant", "meaning_included",
            "meaning_decision_reason", "raw_character_count",
            "weighted_character_count", "verification_label", "source_class",
            "historical_confidence", "shortening_applied",
            "confidence_dimensions", "source_role_audit_version", "rendering_mode",
        )
    }
    metadata["source_role_audit_version"] = (
        "historical-context-source-roles-v2-recovered-citations"
    )
    metadata["formatter_version"] = context_module.HISTORICAL_CONTEXT_FORMATTER_V4

    assert HistoricalContextReplyStore._valid_formatter_metadata(metadata) is True


@pytest.mark.parametrize(
    ("parent_post_id", "quote_id", "reply_text"),
    [
        ("not-numeric", "a" * 64, "Context"),
        ("111", "not-a-sha256", "Context"),
        ("111", "a" * 64, "   "),
    ],
)
def test_invalid_context_request_is_rejected_before_remote_post(
    tmp_path, parent_post_id, quote_id, reply_text
):
    store = _historical_store(tmp_path / "history.json", tmp_path / "receipt.json")

    with pytest.raises(ValueError, match="invalid historical context reply request"):
        store.post(
            parent_post_id=parent_post_id,
            quote_id=quote_id,
            reply_text=reply_text,
            create_post=lambda **kwargs: pytest.fail("invalid request must not reach remote post"),
            now_epoch=lambda: 123,
        )


@pytest.mark.parametrize("raced_kind", ["file", "symlink"])
def test_initial_sending_publication_does_not_overwrite_raced_entry(
    raced_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path = tmp_path / "receipt.json"
    store = _historical_store(tmp_path / "history.json", receipt_path)
    raced_bytes = b'{"peer":"existing-authority"}\n'
    target_path = tmp_path / "peer-target.json"
    real_publish = context_module.publish_exact_source_receipt_document

    def inject_raced_entry(path: Path, **kwargs: object) -> None:
        assert Path(path) == receipt_path
        if raced_kind == "file":
            receipt_path.write_bytes(raced_bytes)
            receipt_path.chmod(0o600)
        else:
            target_path.write_bytes(raced_bytes)
            receipt_path.symlink_to(target_path.name)
        real_publish(path, **kwargs)

    monkeypatch.setattr(
        context_module,
        "publish_exact_source_receipt_document",
        inject_raced_entry,
    )

    with pytest.raises(
        AmbiguousContextReplyOutcome,
        match="appeared during publication",
    ):
        store.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=lambda **_kwargs: pytest.fail(
                "a raced receipt entry must block before remote transport"
            ),
            now_epoch=lambda: 123,
        )

    if raced_kind == "file":
        assert not receipt_path.is_symlink()
        assert receipt_path.read_bytes() == raced_bytes
    else:
        assert receipt_path.is_symlink()
        assert receipt_path.readlink() == Path(target_path.name)
        assert target_path.read_bytes() == raced_bytes
    assert not store.history_path.exists()


def test_confirmed_receipt_reconciles_after_restart_without_posting(tmp_path):
    receipt = {"schema_version": 1, "parent_post_id": "111", "reply_post_id": "222",
        "quote_id": "a" * 64, "reply_text": "Context", "reply_epoch": 123, "confirmed_at": "now"}
    context_module.atomic_write_json(tmp_path / "receipt.json", receipt)
    restarted = _historical_store(tmp_path / "history.json", tmp_path / "receipt.json")
    assert restarted.reconcile_receipt() is True and not restarted.receipt_path.exists()
    result = restarted.post(parent_post_id="111", quote_id="a" * 64, reply_text="Context",
        create_post=lambda **kwargs: pytest.fail("reconciled receipt must prevent duplicate"), now_epoch=lambda: 124)
    assert result["status"] == "already_completed"


def test_context_receipt_reader_rejects_symlink_without_following_it(tmp_path):
    target = tmp_path / "target.json"
    receipt = {
        "schema_version": 1,
        "parent_post_id": "111",
        "reply_post_id": "222",
        "quote_id": "a" * 64,
        "reply_text": "Context",
        "reply_epoch": 123,
        "confirmed_at": "now",
    }
    target.write_text(json.dumps(receipt), encoding="utf-8")
    receipt_path = tmp_path / "receipt.json"
    receipt_path.symlink_to(target.name)
    store = _historical_store(tmp_path / "history.json", receipt_path)

    with pytest.raises(RuntimeError, match="unsafe filesystem metadata"):
        store.reconcile_receipt()

    assert receipt_path.is_symlink()
    assert json.loads(target.read_text(encoding="utf-8")) == receipt
    assert not store.history_path.exists()


def test_context_receipt_reader_rejects_same_byte_aba_replacement(
    tmp_path,
    monkeypatch,
):
    receipt = {
        "schema_version": 1,
        "parent_post_id": "111",
        "reply_post_id": "222",
        "quote_id": "a" * 64,
        "reply_text": "Context",
        "reply_epoch": 123,
        "confirmed_at": "now",
    }
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    store = _historical_store(tmp_path / "history.json", receipt_path)
    real_lstat = context_module.os.lstat
    receipt_lstats = 0

    def replace_before_final_lstat(path):
        nonlocal receipt_lstats
        if Path(path) == receipt_path:
            receipt_lstats += 1
            if receipt_lstats == 2:
                replacement = tmp_path / "replacement.json"
                replacement.write_bytes(receipt_path.read_bytes())
                replacement.replace(receipt_path)
        return real_lstat(path)

    monkeypatch.setattr(context_module.os, "lstat", replace_before_final_lstat)

    with pytest.raises(RuntimeError, match="changed while it was read"):
        store.reconcile_receipt()

    assert receipt_lstats >= 2
    assert receipt_path.exists()
    assert not store.history_path.exists()


def test_context_receipt_duplicate_lifecycle_cannot_fabricate_confirmation(
    tmp_path,
):
    """Duplicate names cannot retire a sending ambiguity as confirmed."""

    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(
        """{
  "schema_version": 1,
  "lifecycle_state": "sending",
  "parent_post_id": "111",
  "quote_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "reply_text": "Context",
  "reply_epoch": 1800000000,
  "started_at": "2026-07-31T12:00:00Z",
  "attempt_number": 1,
  "lifecycle_state": "confirmed",
  "reply_post_id": "999",
  "confirmed_at": "2026-07-31T12:00:01Z"
}
""",
        encoding="utf-8",
    )
    receipt_bytes = receipt_path.read_bytes()
    store = _historical_store(tmp_path / "history.json", receipt_path)

    assert (
        HistoricalContextReplyStore.receipt_parent_for_safe_local_reconciliation(
            receipt_path
        )
        is None
    )
    with pytest.raises(RuntimeError, match="invalid context reply receipt JSON"):
        store.reconcile_receipt()

    assert receipt_path.read_bytes() == receipt_bytes
    assert not store.history_path.exists()


def test_context_receipt_retirement_preserves_barrier_on_path_replacement(
    tmp_path,
    monkeypatch,
):
    receipt = {
        "schema_version": 1,
        "parent_post_id": "111",
        "reply_post_id": "222",
        "quote_id": "a" * 64,
        "reply_text": "Context",
        "reply_epoch": 123,
        "confirmed_at": "now",
    }
    replacement = {**receipt, "parent_post_id": "333", "reply_post_id": "444"}
    receipt_path = tmp_path / "receipt.json"
    context_module.atomic_write_json(receipt_path, receipt)
    store = _historical_store(tmp_path / "history.json", receipt_path)
    real_retire = context_module.retire_or_resume_exact_receipt

    def replace_then_retire(path, expected_bytes, **kwargs):
        context_module.atomic_write_json(receipt_path, replacement)
        return real_retire(path, expected_bytes, **kwargs)

    monkeypatch.setattr(
        context_module,
        "retire_or_resume_exact_receipt",
        replace_then_retire,
    )

    with pytest.raises(RuntimeError, match="fresh receipt requires"):
        store.reconcile_receipt()

    assert json.loads(receipt_path.read_text(encoding="utf-8")) == replacement
    assert store.history()["items"]["111"]["status"] == "completed"


@pytest.mark.parametrize(
    "patch",
    [
        {"schema_version": 2},
        {"quote_id": "not-a-sha256"},
        {"reply_epoch": "not-an-epoch"},
        {"reply_text": ""},
    ],
)
def test_malformed_context_receipt_is_blocked_without_mutating_history(tmp_path, patch):
    receipt = {
        "schema_version": 1,
        "parent_post_id": "111",
        "reply_post_id": "222",
        "quote_id": "a" * 64,
        "reply_text": "Context",
        "reply_epoch": 123,
        "confirmed_at": "now",
        **patch,
    }
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt))
    store = _historical_store(tmp_path / "history.json", receipt_path)

    with pytest.raises(RuntimeError, match="invalid historical context reply receipt"):
        store.reconcile_receipt()

    assert receipt_path.exists()
    assert not store.history_path.exists()


def test_context_store_rejects_boolean_schema_versions(tmp_path):
    history_path = tmp_path / "history.json"
    receipt_path = tmp_path / "receipt.json"
    history_path.write_text(json.dumps({"schema_version": True, "items": {}}))
    store = _historical_store(history_path, receipt_path)

    with pytest.raises(RuntimeError, match="invalid context reply history"):
        store.history()

    history_path.unlink()
    receipt_path.write_text(json.dumps({
        "schema_version": True,
        "lifecycle_state": "sending",
        "parent_post_id": "111",
        "quote_id": "a" * 64,
        "reply_text": "Context",
        "reply_epoch": 123,
        "started_at": "now",
        "attempt_number": 1,
    }))
    with pytest.raises(RuntimeError, match="invalid historical context reply receipt"):
        store.reconcile_receipt()


def test_conflicting_completed_receipt_is_blocked_without_overwrite(tmp_path):
    history_path = tmp_path / "history.json"
    receipt_path = tmp_path / "receipt.json"
    existing = {
        "schema_version": 1,
        "items": {"111": {
            "schema_version": 1,
            "parent_post_id": "111",
            "reply_post_id": "222",
            "quote_id": "a" * 64,
            "reply_text": "Context",
            "reply_epoch": 123,
            "confirmed_at": "now",
            "status": "completed",
        }},
    }
    conflicting = {**existing["items"]["111"], "reply_post_id": "333"}
    conflicting.pop("status")
    history_path.write_text(json.dumps(existing))
    receipt_path.write_text(json.dumps(conflicting))
    store = _historical_store(history_path, receipt_path)

    with pytest.raises(RuntimeError, match="conflicts with completed history"):
        store.reconcile_receipt()

    assert json.loads(history_path.read_text()) == existing
    assert receipt_path.exists()


def test_malformed_completed_history_cannot_suppress_reply(tmp_path):
    history_path = tmp_path / "history.json"
    history_path.write_text(json.dumps({
        "schema_version": 1,
        "items": {"111": {
            "parent_post_id": "111",
            "quote_id": "a" * 64,
            "reply_text": "Context",
            "status": "completed",
        }},
    }))
    store = _historical_store(history_path, tmp_path / "receipt.json")

    with pytest.raises(RuntimeError, match="invalid completed context reply history"):
        store.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=lambda **kwargs: pytest.fail("invalid history must block before posting"),
            now_epoch=lambda: 123,
        )


def test_failure_is_recorded_and_can_be_replayed_without_main_post(tmp_path):
    store = _historical_store(tmp_path / "history.json", tmp_path / "receipt.json")
    failed = store.post(parent_post_id="111", quote_id="a" * 64, reply_text="Context",
        create_post=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
        now_epoch=lambda: 123,
        remote_failure_is_definite_non_success=lambda _error: True)
    assert failed["status"] == "failed"
    recovered = store.post(parent_post_id="111", quote_id="a" * 64, reply_text="Context",
        create_post=lambda **kwargs: {"data": {"id": "223"}}, now_epoch=lambda: 124)
    assert recovered["status"] == "completed"


def test_keyboard_interrupt_is_not_swallowed_or_recorded_as_provider_failure(tmp_path):
    store = _historical_store(tmp_path / "history.json", tmp_path / "receipt.json")

    with pytest.raises(KeyboardInterrupt):
        store.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=lambda **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
            now_epoch=lambda: 123,
        )

    assert not store.history_path.exists()
    sending = json.loads(store.receipt_path.read_text())
    assert sending["lifecycle_state"] == "sending"

    restarted = _historical_store(store.history_path, store.receipt_path)
    with pytest.raises(AmbiguousContextReplyOutcome):
        restarted.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=lambda **kwargs: pytest.fail("ambiguous request must never be repeated"),
            now_epoch=lambda: 124,
        )


def test_confirmed_receipt_write_failure_leaves_sending_barrier(tmp_path, monkeypatch):
    store = _historical_store(tmp_path / "history.json", tmp_path / "receipt.json")
    real_atomic_write = context_module.atomic_write_json

    def fail_confirmed(path, value, **kwargs):
        if value.get("lifecycle_state") == "confirmed":
            raise OSError("disk full")
        return real_atomic_write(path, value, **kwargs)

    monkeypatch.setattr(context_module, "atomic_write_json", fail_confirmed)
    with pytest.raises(AmbiguousContextReplyOutcome, match="confirmed reply receipt"):
        store.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=lambda **kwargs: {"data": {"id": "222"}},
            now_epoch=lambda: 123,
        )

    assert json.loads(store.receipt_path.read_text())["lifecycle_state"] == "sending"


def test_failure_history_write_failure_retains_sending_barrier(tmp_path, monkeypatch):
    store = _historical_store(tmp_path / "history.json", tmp_path / "receipt.json")
    real_atomic_write = context_module.atomic_write_json

    def fail_history(path, value, **kwargs):
        if "items" in value:
            raise OSError("history disk full")
        return real_atomic_write(path, value, **kwargs)

    monkeypatch.setattr(context_module, "atomic_write_json", fail_history)
    with pytest.raises(AmbiguousContextReplyOutcome, match="failure history"):
        store.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("confirmed failure")),
            now_epoch=lambda: 123,
            remote_failure_is_definite_non_success=lambda _error: True,
        )

    assert json.loads(store.receipt_path.read_text())["lifecycle_state"] == "sending"


def test_recorded_failure_reconciles_stale_sending_marker_by_attempt_number(tmp_path, monkeypatch):
    store = _historical_store(tmp_path / "history.json", tmp_path / "receipt.json")
    real_retire = context_module.retire_or_resume_exact_receipt
    monkeypatch.setattr(
        context_module,
        "retire_or_resume_exact_receipt",
        lambda path, expected, **_kwargs: (_ for _ in ()).throw(
            OSError("directory fsync failed")
        ),
    )

    with pytest.raises(AmbiguousContextReplyOutcome, match="clear context reply sending record"):
        store.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("confirmed failure")),
            now_epoch=lambda: 123,
            remote_failure_is_definite_non_success=lambda _error: True,
        )

    sending = json.loads(store.receipt_path.read_text())
    failed = store.history()["items"]["111"]
    assert sending["attempt_number"] == failed["attempt_count"] == 1

    monkeypatch.setattr(
        context_module,
        "retire_or_resume_exact_receipt",
        real_retire,
    )
    assert store.reconcile_receipt() is False
    assert not store.receipt_path.exists()


def test_dry_run_makes_no_post_and_includes_count(tmp_path):
    store = _historical_store(tmp_path / "history.json", tmp_path / "receipt.json")
    result = store.post(parent_post_id="111", quote_id="a" * 64, reply_text="Context",
        create_post=lambda **kwargs: pytest.fail("dry run must not post"), now_epoch=lambda: 123, dry_run=True)
    assert result == {"status": "dry_run", "parent_post_id": "111", "quote_id": "a" * 64,
                      "reply_text": "Context", "character_count": 7}
    assert not store.history_path.exists() and not store.receipt_path.exists()


def test_dry_run_does_not_reconcile_or_remove_an_existing_receipt(tmp_path):
    store = _historical_store(tmp_path / "history.json", tmp_path / "receipt.json")
    receipt = {
        "schema_version": 1,
        "parent_post_id": "111",
        "reply_post_id": "222",
        "quote_id": "a" * 64,
        "reply_text": "Previously confirmed context.",
        "reply_epoch": 123,
        "confirmed_at": "2026-07-19T08:00:00Z",
    }
    store.receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    receipt_before = store.receipt_path.read_bytes()

    result = store.post(
        parent_post_id="111",
        quote_id="a" * 64,
        reply_text="Dry run context.",
        create_post=lambda **kwargs: pytest.fail("dry run must not post"),
        now_epoch=lambda: 456,
        dry_run=True,
    )

    assert result["status"] == "dry_run"
    assert store.receipt_path.read_bytes() == receipt_before
    assert not store.history_path.exists()

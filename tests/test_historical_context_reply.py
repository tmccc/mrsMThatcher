from __future__ import annotations

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
    format_context_reply_v2,
    load_and_validate_corpus,
    packet_for_posted_quote,
    quote_text_hash,
    select_primary_source,
    x_weighted_length,
)

RESEARCH = Path("semantic_alignment_research/quote_research_full_001")


@pytest.fixture(scope="module")
def corpus():
    return load_and_validate_corpus(RESEARCH)


def test_runtime_context_reply_state_files_are_gitignored():
    patterns = set(Path(".gitignore").read_text().splitlines())
    assert "historical_context_reply_history.json" in patterns
    assert "historical_context_reply_receipt.json" in patterns


def test_completed_corpus_validation_and_unresolved_rejection(corpus):
    packets, unresolved = corpus
    assert len(packets) == 626 and len(unresolved) == 6 and not set(packets) & unresolved
    assert packet_for_posted_quote(packets, unresolved, next(iter(unresolved)), "anything") is None


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

    with pytest.raises(RuntimeError, match="exactly 632 unique manifest records"):
        load_and_validate_corpus(research)


def test_duplicate_unresolved_status_id_is_rejected_before_set_collapse(tmp_path):
    research = tmp_path / "research"
    (research / "final_unresolved").mkdir(parents=True)
    shutil.copy(RESEARCH / "research_packets.json", research / "research_packets.json")
    shutil.copy(RESEARCH / "corpus_manifest.json", research / "corpus_manifest.json")
    status = json.loads((RESEARCH / "final_unresolved" / "final_research_status.json").read_text())
    status["unresolved_quote_ids"].append(status["unresolved_quote_ids"][0])
    (research / "final_unresolved" / "final_research_status.json").write_text(json.dumps(status))

    with pytest.raises(RuntimeError, match="exactly six unique unresolved quote IDs"):
        load_and_validate_corpus(research)


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
    validate_json_schema(format_context_reply_v2(packet), schema)


def test_standalone_formatter_cli_uses_production_v2(corpus, capsys):
    packet = next(iter(corpus[0].values()))
    assert context_module.main([
        "--research-dir", str(RESEARCH), "--quote-id", packet["quote_id"],
    ]) == 0
    output = capsys.readouterr().out
    assert output.startswith("Context — ")
    assert "Historical context\n" not in output


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

    assert text.startswith("Historical context\nOccasion: Speech to the Fraser Institute.\n")
    assert "\n\nMeaning: Capitalism is inherently moral" in text
    assert "\nVerification: Exact wording\n" in text
    assert "\nSource: Margaret Thatcher Foundation Archive, November 8, 1993" in text
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
    store = HistoricalContextReplyStore(tmp_path / "history.json", tmp_path / "receipt.json")
    calls = []
    create = lambda **kwargs: calls.append(kwargs) or {"data": {"id": "222"}}
    result = store.post(parent_post_id="111", quote_id="a" * 64, reply_text="Context", create_post=create, now_epoch=lambda: 123)
    assert result["status"] == "completed" and not store.receipt_path.exists()
    again = store.post(parent_post_id="111", quote_id="a" * 64, reply_text="Context", create_post=create, now_epoch=lambda: 124)
    assert again["status"] == "already_completed" and len(calls) == 1
    assert json.loads(store.history_path.read_text())["items"]["111"]["reply_post_id"] == "222"


def test_completed_parent_with_different_quote_identity_is_conflict(tmp_path):
    store = HistoricalContextReplyStore(tmp_path / "history.json", tmp_path / "receipt.json")
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
    store = HistoricalContextReplyStore(tmp_path / "history.json", tmp_path / "receipt.json")

    with pytest.raises(RuntimeError, match="clock unavailable"):
        store.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=lambda **kwargs: pytest.fail("clock failure must occur before remote post"),
            now_epoch=lambda: (_ for _ in ()).throw(RuntimeError("clock unavailable")),
        )


def test_overlong_numeric_response_id_is_failure_not_invalid_receipt(tmp_path):
    store = HistoricalContextReplyStore(tmp_path / "history.json", tmp_path / "receipt.json")
    result = store.post(
        parent_post_id="111",
        quote_id="a" * 64,
        reply_text="Context",
        create_post=lambda **kwargs: {"data": {"id": "2" * 31}},
        now_epoch=lambda: 123,
    )
    assert result["status"] == "failed"
    assert not store.receipt_path.exists()
    assert store.history()["items"]["111"]["status"] == "failed"


def test_long_reply_is_sent_unchanged_through_existing_post_path(tmp_path):
    store = HistoricalContextReplyStore(tmp_path / "history.json", tmp_path / "receipt.json")
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
    assert calls == [{
        "text": reply,
        "media_ids": None,
        "reply_to_id": "111",
        "made_with_ai": False,
    }]


def test_formatter_v2_metadata_is_durable_and_prevents_duplicate_after_restart(tmp_path, corpus):
    packet = next(iter(corpus[0].values()))
    formatted = format_context_reply_v2(packet)
    assert formatted is not None
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
    }
    calls = []
    store = HistoricalContextReplyStore(tmp_path / "history.json", tmp_path / "receipt.json")
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

    restarted = HistoricalContextReplyStore(store.history_path, store.receipt_path)
    duplicate = restarted.post(
        parent_post_id="111",
        quote_id=packet["quote_id"],
        reply_text=formatted["text"],
        formatter_metadata=metadata,
        create_post=lambda **kwargs: pytest.fail("completed v2 reply must not be duplicated"),
        now_epoch=lambda: 124,
    )
    assert duplicate["status"] == "already_completed"
    assert len(calls) == 1


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
    store = HistoricalContextReplyStore(tmp_path / "history.json", tmp_path / "receipt.json")

    with pytest.raises(ValueError, match="invalid historical context reply request"):
        store.post(
            parent_post_id=parent_post_id,
            quote_id=quote_id,
            reply_text=reply_text,
            create_post=lambda **kwargs: pytest.fail("invalid request must not reach remote post"),
            now_epoch=lambda: 123,
        )


def test_confirmed_receipt_reconciles_after_restart_without_posting(tmp_path):
    receipt = {"schema_version": 1, "parent_post_id": "111", "reply_post_id": "222",
        "quote_id": "a" * 64, "reply_text": "Context", "reply_epoch": 123, "confirmed_at": "now"}
    (tmp_path / "receipt.json").write_text(json.dumps(receipt))
    restarted = HistoricalContextReplyStore(tmp_path / "history.json", tmp_path / "receipt.json")
    assert restarted.reconcile_receipt() is True and not restarted.receipt_path.exists()
    result = restarted.post(parent_post_id="111", quote_id="a" * 64, reply_text="Context",
        create_post=lambda **kwargs: pytest.fail("reconciled receipt must prevent duplicate"), now_epoch=lambda: 124)
    assert result["status"] == "already_completed"


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
    store = HistoricalContextReplyStore(tmp_path / "history.json", receipt_path)

    with pytest.raises(RuntimeError, match="invalid historical context reply receipt"):
        store.reconcile_receipt()

    assert receipt_path.exists()
    assert not store.history_path.exists()


def test_context_store_rejects_boolean_schema_versions(tmp_path):
    history_path = tmp_path / "history.json"
    receipt_path = tmp_path / "receipt.json"
    history_path.write_text(json.dumps({"schema_version": True, "items": {}}))
    store = HistoricalContextReplyStore(history_path, receipt_path)

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
    store = HistoricalContextReplyStore(history_path, receipt_path)

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
    store = HistoricalContextReplyStore(history_path, tmp_path / "receipt.json")

    with pytest.raises(RuntimeError, match="invalid completed context reply history"):
        store.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=lambda **kwargs: pytest.fail("invalid history must block before posting"),
            now_epoch=lambda: 123,
        )


def test_failure_is_recorded_and_can_be_replayed_without_main_post(tmp_path):
    store = HistoricalContextReplyStore(tmp_path / "history.json", tmp_path / "receipt.json")
    failed = store.post(parent_post_id="111", quote_id="a" * 64, reply_text="Context",
        create_post=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("offline")), now_epoch=lambda: 123)
    assert failed["status"] == "failed"
    recovered = store.post(parent_post_id="111", quote_id="a" * 64, reply_text="Context",
        create_post=lambda **kwargs: {"data": {"id": "223"}}, now_epoch=lambda: 124)
    assert recovered["status"] == "completed"


def test_keyboard_interrupt_is_not_swallowed_or_recorded_as_provider_failure(tmp_path):
    store = HistoricalContextReplyStore(tmp_path / "history.json", tmp_path / "receipt.json")

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

    restarted = HistoricalContextReplyStore(store.history_path, store.receipt_path)
    with pytest.raises(AmbiguousContextReplyOutcome):
        restarted.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=lambda **kwargs: pytest.fail("ambiguous request must never be repeated"),
            now_epoch=lambda: 124,
        )


def test_confirmed_receipt_write_failure_leaves_sending_barrier(tmp_path, monkeypatch):
    store = HistoricalContextReplyStore(tmp_path / "history.json", tmp_path / "receipt.json")
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
    store = HistoricalContextReplyStore(tmp_path / "history.json", tmp_path / "receipt.json")
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
        )

    assert json.loads(store.receipt_path.read_text())["lifecycle_state"] == "sending"


def test_recorded_failure_reconciles_stale_sending_marker_by_attempt_number(tmp_path, monkeypatch):
    store = HistoricalContextReplyStore(tmp_path / "history.json", tmp_path / "receipt.json")
    real_unlink = context_module.durable_unlink
    monkeypatch.setattr(
        context_module,
        "durable_unlink",
        lambda path: (_ for _ in ()).throw(OSError("directory fsync failed")),
    )

    with pytest.raises(AmbiguousContextReplyOutcome, match="clear context reply sending record"):
        store.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("confirmed failure")),
            now_epoch=lambda: 123,
        )

    sending = json.loads(store.receipt_path.read_text())
    failed = store.history()["items"]["111"]
    assert sending["attempt_number"] == failed["attempt_count"] == 1

    monkeypatch.setattr(context_module, "durable_unlink", real_unlink)
    assert store.reconcile_receipt() is False
    assert not store.receipt_path.exists()


def test_dry_run_makes_no_post_and_includes_count(tmp_path):
    store = HistoricalContextReplyStore(tmp_path / "history.json", tmp_path / "receipt.json")
    result = store.post(parent_post_id="111", quote_id="a" * 64, reply_text="Context",
        create_post=lambda **kwargs: pytest.fail("dry run must not post"), now_epoch=lambda: 123, dry_run=True)
    assert result == {"status": "dry_run", "parent_post_id": "111", "quote_id": "a" * 64,
                      "reply_text": "Context", "character_count": 7}
    assert not store.history_path.exists() and not store.receipt_path.exists()

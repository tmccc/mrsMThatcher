"""Regression tests for bot historical context."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    isolate_bot_runtime,
    configure_simple_quote_post,
)


pytestmark = pytest.mark.allow_loopback_network


def _sourced_packet(quote_id: str) -> dict[str, str]:
    """Supply supported research for tests of delivery and receipt metadata."""
    return {
        "quote_id": quote_id, "quote_text": "Quote",
        "verification_status": "exact", "research_confidence": "high",
        "stable_locator": "Reviewed fixture transcript, page 1",
    }


def test_regular_post_context_stage_runs_only_after_durable_main_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, *_ = configure_simple_quote_post(tmp_path, monkeypatch)
    saved = {"done": False}
    context_calls = []
    original_save = bot.save_regular_post_protected_state

    def tracked_save(*args, **kwargs):
        proof = original_save(*args, **kwargs)
        saved["done"] = True
        return proof

    def context(**kwargs):
        assert saved["done"] is True
        assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
        assert bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.exists()
        context_calls.append(kwargs)
        return {"status": "completed", "reply_post_id": "960001"}

    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(bot, "save_regular_post_protected_state", tracked_save)
    monkeypatch.setattr(bot, "maybe_post_historical_context_reply", context)
    bot.post_random_quote(lines_used, images_used, state)
    assert len(context_calls) == 1 and context_calls[0]["parent_post_id"] == "950001"


def test_context_failure_does_not_undo_confirmed_regular_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, *_ = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(bot, "maybe_post_historical_context_reply", lambda **kwargs: {"status": "failed"})
    bot.post_random_quote(lines_used, images_used, state)
    assert bot.quote_text_hash("Good quote.") in lines_used and state["last_main_post_id"] == "950001"


def test_unpersisted_context_failure_retains_only_auxiliary_outbox_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, *_ = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("context preparation failed")),
    )

    bot.post_random_quote(lines_used, images_used, state)

    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
    outbox = bot.historical_context_outbox_store().get("950001")
    assert outbox["main_post"]["state"] == "main_post_confirmed"
    assert outbox["context_reply"]["state"] == "context_reply_failed_retryable"
    assert bot.quote_text_hash("Good quote.") in lines_used
    assert state["last_main_post_id"] == "950001"


def test_context_reply_disabled_configuration_makes_no_post(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "historical_context_reply", {**bot.historical_context_reply, "enabled": False})
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("disabled context must not post"))
    assert bot.maybe_post_historical_context_reply(
        quote_hash="a" * 64, quote_text="Quote", parent_post_id="123"
    ) == {"status": "disabled"}


def test_ambiguous_context_outcome_propagates_for_manual_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from historical_context_formatter import AmbiguousContextReplyOutcome, HistoricalContextReplyStore

    research = Path(__file__).resolve().parents[1] / "semantic_alignment_research" / "quote_research_full_001"
    packets = json.loads((research / "research_packets.json").read_text())["items"]
    packet = next(iter(packets.values()))
    monkeypatch.setattr(bot, "historical_context_reply", {**bot.historical_context_reply, "enabled": True})
    monkeypatch.setattr(bot, "HISTORICAL_CONTEXT_RESEARCH_DIR", research)
    monkeypatch.setattr(bot, "HISTORICAL_CONTEXT_REPLY_HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(
        HistoricalContextReplyStore,
        "post",
        lambda self, **kwargs: (_ for _ in ()).throw(AmbiguousContextReplyOutcome("ambiguous")),
    )

    with pytest.raises(AmbiguousContextReplyOutcome):
        bot.maybe_post_historical_context_reply(
            quote_hash=packet["quote_id"],
            quote_text=packet["quote_text"],
            parent_post_id="123",
        )


def test_context_sigint_guard_spans_complete_transaction_store_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import historical_context_formatter as context_module

    quote_id = "a" * 64
    packet = _sourced_packet(quote_id)
    formatted = {
        "quote_id": quote_id,
        "text": "Context — Reviewed event.",
        "character_count": 25,
        "weighted_character_count": 25,
        "raw_character_count": 25,
        "maximum_length": 4000,
        "historical_confidence": "high",
        "meaning_included": False,
        "meaning_omitted": True,
        "meaning_decision_reason": "Meaning is redundant.",
        "shortening_applied": False,
        "verification_label": "Exact wording",
        "verification_omitted": False,
        "source": {"title": "Source", "url": "", "source_type": "official"},
        "source_class": "original speech transcript",
        "source_omitted": False,
        "formatter_version": context_module.HISTORICAL_CONTEXT_FORMATTER_V5,
        "confidence_dimensions": {
            "attribution": "high",
            "wording": "high",
            "source_event": "high",
            "date": "high",
            "historical_context": "high",
            "interpretation": "high",
        },
        "source_role_audit_version": "test-source-role-audit-v1",
        "rendering_mode": "public",
        "template_variant": "compact_without_redundant_meaning",
    }
    guard = object()
    order: list[str] = []
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(
        context_module,
        "load_and_validate_corpus",
        lambda *_args, **_kwargs: ({quote_id: packet}, set()),
    )
    monkeypatch.setattr(
        context_module,
        "packet_for_posted_quote",
        lambda *_args: packet,
    )
    monkeypatch.setattr(
        context_module,
        "format_context_reply_public",
        lambda *_args, **_kwargs: formatted,
    )

    def store_post(_self: object, **kwargs: object) -> dict[str, object]:
        order.append("store")
        assert kwargs["require_confirmed_transport"] is True
        assert order == ["begin", "store"]
        return {"status": "completed", "reply_post_id": "456"}

    monkeypatch.setattr(context_module.HistoricalContextReplyStore, "post", store_post)
    monkeypatch.setattr(
        bot,
        "begin_confirmed_post_sigint_deferral",
        lambda: order.append("begin") or guard,
    )

    def end(actual: object) -> None:
        assert actual is guard
        order.append("end")

    monkeypatch.setattr(bot, "end_confirmed_post_sigint_deferral", end)

    result = bot.maybe_post_historical_context_reply(
        quote_hash=quote_id,
        quote_text="Quote",
        parent_post_id="123",
    )

    assert result["status"] == "completed"
    assert order == ["begin", "store", "end"]


def test_unpersisted_context_preparation_failure_propagates_for_main_receipt_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import historical_context_formatter as context_module

    monkeypatch.setattr(bot, "historical_context_reply", {**bot.historical_context_reply, "enabled": True})
    monkeypatch.setattr(
        context_module,
        "load_and_validate_corpus",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("corpus temporarily unreadable")),
    )

    with pytest.raises(RuntimeError, match="corpus temporarily unreadable"):
        bot.maybe_post_historical_context_reply(
            quote_hash="a" * 64,
            quote_text="Quote",
            parent_post_id="123",
        )


def test_main_context_reply_path_uses_public_v5_and_persists_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import historical_context_formatter as context_module

    quote_id = "a" * 64
    packet = _sourced_packet(quote_id)
    formatted = {
        "quote_id": quote_id,
        "text": "Context — Compact historical context.",
        "character_count": 38,
        "weighted_character_count": 38,
        "raw_character_count": 38,
        "maximum_length": 4000,
        "historical_confidence": "high",
        "meaning_included": False,
        "meaning_omitted": True,
        "meaning_decision_reason": "Meaning is redundant.",
        "shortening_applied": False,
        "verification_label": "Exact wording",
        "verification_omitted": False,
        "source": {"title": "Source", "url": "", "source_type": "official"},
        "source_class": "original speech transcript",
        "source_omitted": False,
        "formatter_version": context_module.HISTORICAL_CONTEXT_FORMATTER_V5,
        "confidence_dimensions": {
            "attribution": "high",
            "wording": "high",
            "source_event": "high",
            "date": "high",
            "historical_context": "high",
            "interpretation": "high",
        },
        "source_role_audit_version": "test-source-role-audit-v1",
        "rendering_mode": "public",
        "template_variant": "compact_without_redundant_meaning",
    }
    calls = []
    format_calls = []
    events = []
    monkeypatch.setattr(bot, "historical_context_reply", {**bot.historical_context_reply, "enabled": True})
    monkeypatch.setattr(bot, "HISTORICAL_CONTEXT_RESEARCH_DIR", tmp_path / "research")
    monkeypatch.setattr(bot, "HISTORICAL_CONTEXT_REPLY_HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(
        context_module,
        "load_and_validate_corpus",
        lambda _path, **_kwargs: ({quote_id: packet}, set()),
    )
    monkeypatch.setattr(context_module, "packet_for_posted_quote", lambda *_args: packet)
    monkeypatch.setattr(context_module, "format_context_reply", lambda *_args, **_kwargs: pytest.fail("v1 must not be used"))
    monkeypatch.setattr(
        context_module,
        "format_context_reply_public",
        lambda *args, **kwargs: format_calls.append((args, kwargs)) or formatted,
    )
    monkeypatch.setattr(
        context_module.HistoricalContextReplyStore,
        "post",
        lambda self, **kwargs: calls.append(kwargs) or {"status": "completed", "reply_post_id": "456"},
    )
    monkeypatch.setattr(bot, "log_event", lambda event, **fields: events.append({"event": event, **fields}))

    result = bot.maybe_post_historical_context_reply(
        quote_hash=quote_id,
        quote_text="Quote",
        parent_post_id="123",
    )

    assert result["status"] == "completed"
    assert calls[0]["reply_text"] == formatted["text"]
    assert len(format_calls) == 1
    assert format_calls[0][0] == (packet,)
    assert "rendering_mode" not in format_calls[0][1]
    assert calls[0]["formatter_metadata"]["formatter_version"] == context_module.HISTORICAL_CONTEXT_FORMATTER_V5
    assert calls[0]["formatter_metadata"]["confidence_dimensions"] == formatted["confidence_dimensions"]
    assert calls[0]["formatter_metadata"]["source_role_audit_version"] == formatted["source_role_audit_version"]
    assert calls[0]["formatter_metadata"]["rendering_mode"] == "public"
    assert calls[0]["formatter_metadata"]["template_variant"] == formatted["template_variant"]
    context_event = next(
        event
        for event in events
        if event["event"] == "historical_context_reply"
    )
    assert context_event["formatter_version"] == context_module.HISTORICAL_CONTEXT_FORMATTER_V5
    assert context_event["rendering_mode"] == "public"
    assert events[-1]["event"] == "historical_context_reply_posted"


def test_already_completed_legacy_context_reply_is_not_relabelled_as_v4(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import historical_context_formatter as context_module

    quote_id = "a" * 64
    packet = _sourced_packet(quote_id)
    v4 = {
        "quote_id": quote_id, "text": "Context — New v4 text.", "character_count": 22,
        "weighted_character_count": 22, "raw_character_count": 22, "maximum_length": 4000,
        "historical_confidence": "high", "meaning_included": False, "meaning_omitted": True,
        "meaning_decision_reason": "Redundant.", "shortening_applied": False,
        "verification_label": "Exact wording", "verification_omitted": False,
        "source": {"title": "Source", "url": "", "source_type": "official"},
        "source_class": "original speech transcript", "source_omitted": False,
        "formatter_version": context_module.HISTORICAL_CONTEXT_FORMATTER_V4,
        "confidence_dimensions": {
            "attribution": "high", "wording": "high", "source_event": "high",
            "date": "high", "historical_context": "high", "interpretation": "high",
        },
        "source_role_audit_version": "test-source-role-audit-v1",
        "rendering_mode": "public",
        "template_variant": "compact_without_redundant_meaning",
    }
    legacy_text = "Historical context\nOccasion: Legacy event.\n\nVerification: Exact wording\nSource: Legacy"
    events = []
    monkeypatch.setattr(bot, "historical_context_reply", {**bot.historical_context_reply, "enabled": True})
    monkeypatch.setattr(bot, "HISTORICAL_CONTEXT_RESEARCH_DIR", tmp_path / "research")
    monkeypatch.setattr(
        context_module,
        "load_and_validate_corpus",
        lambda _path, **_kwargs: ({quote_id: packet}, set()),
    )
    monkeypatch.setattr(context_module, "packet_for_posted_quote", lambda *_args: packet)
    monkeypatch.setattr(context_module, "format_context_reply_public", lambda *_args, **_kwargs: v4)
    monkeypatch.setattr(
        context_module.HistoricalContextReplyStore,
        "post",
        lambda self, **kwargs: {
            "status": "already_completed", "quote_id": quote_id,
            "parent_post_id": "123", "reply_text": legacy_text,
        },
    )
    monkeypatch.setattr(bot, "log_event", lambda event, **fields: events.append({"event": event, **fields}))

    bot.maybe_post_historical_context_reply(
        quote_hash=quote_id, quote_text="Quote", parent_post_id="123",
    )

    assert events[-1]["formatter_version"] == "historical_context_reply_schema_v1"
    assert events[-1]["raw_character_count"] == len(legacy_text)
    assert events[-1]["character_count"] == context_module.x_weighted_length(legacy_text)

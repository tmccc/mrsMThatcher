"""Suppress incomplete context research before transport without retrying it."""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import historical_context_formatter as formatter
import mrs_bot_historical_context_delivery as delivery
from historical_context_outbox import (
    FAILED_TERMINAL,
    MAIN_POST_CONFIRMED,
    NOT_REQUIRED,
    HistoricalContextOutbox,
)
from mrs_log_digest_historical_events import historical_context_quality_summary
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    configure_simple_quote_post,
    isolate_regular_post_receipt,  # noqa: F401
    quote_analysis_for_lines,
)


RESEARCH = (
    Path(__file__).resolve().parents[1]
    / "semantic_alignment_research" / "quote_research_full_001"
)
INCOMPLETE = "9c84eb3fbb816db3d01e30e4ab2c09b4c57ade38214abaf4d9db4aedb94da8f4"
EXACT_TRANSCRIPT = "00a61fc4f76648e2ccbf07fbdadec99afb0000789e85390bae28f11cb3f230ae"
OFFLINE_BOOK = "3cced21d7f9bc45fd5479288c7b413bad5e0e48fcf71f103251b6284c8528f12"
VERIFIED_VARIANT = "0827a4126cc47bd563b75be766edeffcd44f7027125f190887ffb7a70c5bb015"
ATTRIBUTED_MEDIUM = "0e54df9b0adb2337f436d7346964965c56669c997386d75f2d58104ec88e1766"


@pytest.fixture(scope="module")
def audited_packets():
    return formatter.load_and_validate_corpus(
        RESEARCH, require_source_role_audit=True,
    )[0]


def _install_delivery(monkeypatch, packet):
    runtime = SimpleNamespace(
        store=SimpleNamespace(
            reconcile_receipt=Mock(),
            post=Mock(side_effect=AssertionError("unexpected context transport")),
        ),
        create=Mock(side_effect=AssertionError("unexpected remote create")),
        event=Mock(),
    )
    monkeypatch.setattr(bot, "historical_context_reply", {
        **bot.historical_context_reply, "enabled": True,
    })
    monkeypatch.setattr(
        bot, "_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT",
        ({packet["quote_id"]: packet}, set()),
    )
    monkeypatch.setattr(bot, "historical_context_reply_store", lambda: runtime.store)
    monkeypatch.setattr(bot, "create_post", runtime.create)
    monkeypatch.setattr(bot, "log_event", runtime.event)
    monkeypatch.setattr(bot, "emit_historical_context_reply_posted", Mock())
    return runtime


def _deliver(packet, *, dry_run=False):
    return bot.maybe_post_historical_context_reply(
        quote_hash=packet["quote_id"], quote_text=packet["quote_text"],
        parent_post_id="123", dry_run=dry_run,
    )


def test_real_incomplete_research_overrides_optimistic_original_packet(audited_packets):
    packet = audited_packets[INCOMPLETE]
    assert packet["verification_status"] == "exact"
    assert packet["research_confidence"] == "high"
    rendered = formatter.format_context_reply_public(packet)
    assert rendered["verification_label"] == "Research incomplete"
    assert rendered["sources"] == []
    assert delivery.context_reply_research_is_complete(packet) is False


@pytest.mark.parametrize("missing", ["public_source", "public_verification"])
def test_both_public_source_and_sufficient_verification_are_required(audited_packets, missing):
    packet = copy.deepcopy(audited_packets[OFFLINE_BOOK])
    audit = packet["_source_role_audit"]
    if missing == "public_source":
        audit["renderable_sources"] = []
    else:
        audit["public_verification_wording"] = "Exact wording not independently verified"
        audit["confidence_after"]["attribution"] = "unknown"
        for source in audit["renderable_sources"]:
            source["assigned_roles"] = ["historical_context_support"]
            source["claims_supported"] = ["historical_context"]
    rendered = formatter.format_context_reply_public(packet)
    if missing == "public_source":
        assert rendered["verification_label"] == "Exact wording verified"
        assert rendered["sources"] == []
    else:
        assert rendered["sources"]
        assert rendered["verification_label"] == "Research incomplete"
    assert delivery.context_reply_research_is_complete(packet) is False


@pytest.mark.parametrize("quote_id", [EXACT_TRANSCRIPT, OFFLINE_BOOK, VERIFIED_VARIANT, ATTRIBUTED_MEDIUM])
def test_complete_public_research_remains_eligible(audited_packets, quote_id):
    packet = audited_packets[quote_id]
    assert delivery.context_reply_research_is_complete(packet) is True
    if quote_id == OFFLINE_BOOK:
        sources = formatter.format_context_reply_public(packet)["sources"]
        assert sources and all(not source["url"] for source in sources)
        assert "The Downing Street Years, p. 513" in sources[0]["title"]
    if quote_id == ATTRIBUTED_MEDIUM:
        rendered = formatter.format_context_reply_public(packet)
        assert rendered["historical_confidence"] == "medium"
        assert rendered["verification_label"] == "Attributed, but exact wording not independently verified"
        assert rendered["sources"]


def test_missing_occasion_and_date_do_not_disqualify_a_usable_book_source(audited_packets):
    packet = copy.deepcopy(audited_packets[OFFLINE_BOOK])
    packet["source_event"] = "unknown"
    packet["date"] = "unknown"
    audit = packet["_source_role_audit"]
    for field in ("date", "source_event", "historical_context"):
        audit["confidence_after"][field] = "unknown"
    audit["public_context_supported_fields"] = []
    assert delivery.context_reply_research_is_complete(packet) is True


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("show_evidence", [False, True])
def test_incomplete_delivery_stops_before_format_or_post_even_when_evidence_hidden(
    monkeypatch, audited_packets, capsys, dry_run, show_evidence,
):
    packet = audited_packets[INCOMPLETE]
    runtime = _install_delivery(monkeypatch, packet)
    monkeypatch.setattr(bot, "historical_context_reply", {
        **bot.historical_context_reply,
        "include_source": show_evidence, "include_verification": show_evidence,
    })
    render = Mock(side_effect=AssertionError("incomplete research must not be formatted"))
    monkeypatch.setattr(formatter, "format_context_reply_public", render)
    order = []
    runtime.store.reconcile_receipt.side_effect = lambda: order.append("reconcile")
    original_policy = delivery.context_reply_research_is_complete

    def check(packet):
        order.append("policy")
        return original_policy(packet)

    monkeypatch.setattr(delivery, "context_reply_research_is_complete", check)
    result = _deliver(packet, dry_run=dry_run)
    assert result["status"] == "skipped_incomplete_research"
    assert result["reason"] == "incomplete_historical_research"
    assert result["quote_id"] == INCOMPLETE
    assert order == (["policy"] if dry_run else ["reconcile", "policy"])
    runtime.store.post.assert_not_called()
    runtime.create.assert_not_called()
    render.assert_not_called()
    runtime.event.assert_called_once_with(
        "historical_context_reply", status="skipped_incomplete_research",
        parent_post_id="123", quote_id=INCOMPLETE,
        reason="incomplete_historical_research",
    )
    quality = historical_context_quality_summary([{
        "kind": "historical_context_reply", **runtime.event.call_args.kwargs,
    }])
    assert quality["skip_reason_counts"] == {"incomplete_historical_research": 1}
    assert quality["status_counts"]["skipped"] == 1
    assert quality["status_counts"]["failed"] == quality["attempted_count"] == 0
    assert capsys.readouterr().out == ""


def test_unresolved_receipt_is_not_hidden_by_incomplete_research(monkeypatch, audited_packets):
    packet = audited_packets[INCOMPLETE]
    runtime = _install_delivery(monkeypatch, packet)
    runtime.store.reconcile_receipt.side_effect = formatter.AmbiguousContextReplyOutcome("unresolved receipt")
    check = Mock(side_effect=AssertionError("policy before receipt reconciliation"))
    monkeypatch.setattr(delivery, "context_reply_research_is_complete", check)
    with pytest.raises(formatter.AmbiguousContextReplyOutcome, match="unresolved receipt"):
        _deliver(packet)
    check.assert_not_called()
    runtime.store.post.assert_not_called()


def test_semantic_gate_precedes_research_completeness(monkeypatch, audited_packets):
    packet = audited_packets[INCOMPLETE]
    runtime = _install_delivery(monkeypatch, packet)
    monkeypatch.setattr(
        bot._HISTORICAL_CONTEXT_SEMANTIC_GATE, "disposition", lambda _quote_id: "open_review",
    )
    check = Mock(side_effect=AssertionError("completeness before semantic gate"))
    monkeypatch.setattr(delivery, "context_reply_research_is_complete", check)
    assert _deliver(packet)["status"] == "skipped_future_policy"
    check.assert_not_called()
    runtime.store.post.assert_not_called()


@pytest.mark.parametrize("show_evidence", [False, True])
def test_complete_offline_source_reaches_delivery_with_either_display_setting(
    monkeypatch, audited_packets, show_evidence,
):
    packet = audited_packets[OFFLINE_BOOK]
    runtime = _install_delivery(monkeypatch, packet)
    runtime.store.post.side_effect = None
    runtime.store.post.return_value = {"status": "completed", "reply_post_id": "456"}
    monkeypatch.setattr(bot, "historical_context_reply", {
        **bot.historical_context_reply,
        "include_source": show_evidence, "include_verification": show_evidence,
    })
    assert _deliver(packet)["status"] == "completed"
    runtime.store.post.assert_called_once()
    assert runtime.store.post.call_args.kwargs["quote_id"] == OFFLINE_BOOK
    assert runtime.store.post.call_args.kwargs["require_confirmed_transport"] is True
    runtime.create.assert_not_called()


@pytest.mark.parametrize("prior_failure", [False, True])
def test_skipped_research_is_terminal_and_preserves_confirmed_main_post(
    monkeypatch, audited_packets, prior_failure,
):
    packet = audited_packets[INCOMPLETE]
    runtime = _install_delivery(monkeypatch, packet)
    store = HistoricalContextOutbox(bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE)
    original = store.enqueue(
        "123", main_post_confirmed_epoch=1_800_000_000,
        quote_id=INCOMPLETE, quote_text=packet["quote_text"],
    )
    current_epoch = 1_800_000_000
    if prior_failure:
        store.claim_attempt("123", started_epoch=current_epoch)
        failed = store.record_retryable_failure(
            "123", attempt_number=1, error="earlier preparation failure",
            failed_epoch=current_epoch,
        )
        current_epoch = failed["context_reply"]["next_attempt_epoch"]
    monkeypatch.setattr(bot, "now_epoch", lambda: current_epoch)
    monkeypatch.setattr(bot, "lane_paused", lambda _lane: False)
    outcome = bot._process_due_historical_context_obligations(store=store)
    expected_state = FAILED_TERMINAL if prior_failure else NOT_REQUIRED
    assert outcome == [{
        "parent_post_id": "123", "status": "skipped_incomplete_research",
        "context_reply_state": expected_state,
    }]
    saved = HistoricalContextOutbox(bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE).get("123")
    assert saved["main_post"] == original["main_post"]
    assert saved["main_post"]["state"] == MAIN_POST_CONFIRMED
    if not prior_failure:
        assert saved["context_reply"]["reason"] == "skipped_incomplete_research"
    else:
        assert saved["context_reply"]["attempt_count"] == 2
    assert bot._process_due_historical_context_obligations(store=store) == []
    runtime.store.reconcile_receipt.assert_called_once()
    runtime.store.post.assert_not_called()
    runtime.create.assert_not_called()
    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()


def test_regular_quote_confirmation_survives_skipped_incomplete_context(
    tmp_path, monkeypatch, audited_packets,
):
    packet = audited_packets[INCOMPLETE]
    lines_used, images_used, state, *paths = configure_simple_quote_post(tmp_path, monkeypatch)
    paths[-1].write_text(packet["quote_text"] + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines([packet["quote_text"]]))
    main_create = Mock(wraps=bot.create_post)
    runtime = _install_delivery(monkeypatch, packet)
    monkeypatch.setattr(bot, "create_post", main_create)
    bot.post_random_quote(lines_used, images_used, state)
    assert state["last_main_post_id"] == "950001"
    assert bot.quote_text_hash(packet["quote_text"]) in lines_used
    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
    obligation = bot.historical_context_outbox_store().get("950001")
    assert obligation["main_post"]["state"] == MAIN_POST_CONFIRMED
    assert obligation["context_reply"]["state"] == NOT_REQUIRED
    assert obligation["context_reply"]["reason"] == "skipped_incomplete_research"
    main_create.assert_called_once()
    runtime.store.post.assert_not_called()

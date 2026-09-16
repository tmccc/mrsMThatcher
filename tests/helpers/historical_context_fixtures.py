"""Share historical-context policy builders and opt-in incident isolation."""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import MappingProxyType

import pytest

import historical_context_formatter as context_formatter
import mrsMThatcher2 as bot
from historical_context_reply_semantic_gate import (
    EXPECTED_LEDGER_SHA256,
    EXPECTED_PROJECTION_SHA256,
    HistoricalContextSemanticGate,
)
from remote_write_safety_protocol import (
    ACTIVATION_BASENAME as REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_BASENAME,
)
from tests.helpers.protocol_activation import create_test_protocol_activation


def _gate(
    blocked: dict[str, str] | None = None,
    *,
    available: bool = True,
    reviewed: dict[str, str] | None = None,
) -> HistoricalContextSemanticGate:
    """Build a semantic gate with explicit fixture review dispositions."""

    if not available:
        return HistoricalContextSemanticGate.closed("test gate unavailable")
    return HistoricalContextSemanticGate(
        available=True,
        reason="",
        ledger_sha256=EXPECTED_LEDGER_SHA256,
        projection_sha256=EXPECTED_PROJECTION_SHA256,
        blocked_dispositions=MappingProxyType(dict(blocked or {})),
        reviewed_dispositions=MappingProxyType(dict(reviewed or {})),
    )


def _formatted(quote_id: str) -> dict[str, object]:
    """Build one safe historical-context formatter result."""

    text = "Context — Safe reviewed context."
    return {
        "quote_id": quote_id,
        "text": text,
        "character_count": len(text),
        "weighted_character_count": len(text),
        "raw_character_count": len(text),
        "maximum_length": 4000,
        "historical_confidence": "high",
        "meaning_included": False,
        "meaning_omitted": True,
        "meaning_decision_reason": "Meaning omitted by reviewed formatter.",
        "shortening_applied": False,
        "verification_label": "Exact wording",
        "verification_omitted": False,
        "source": {"title": "Source", "url": "", "source_type": "official"},
        "source_class": "original speech transcript",
        "source_omitted": False,
        "formatter_version": context_formatter.HISTORICAL_CONTEXT_FORMATTER_V5,
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


def _install_bot_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    packet: dict[str, str],
    gate: HistoricalContextSemanticGate,
) -> list[dict[str, object]]:
    # These fixtures isolate semantic policy and delivery. Give their synthetic
    # packets supported research so the separate completeness check can pass.
    """Install a synthetic packet and semantic gate while recording bot events."""

    packet.setdefault("verification_status", "exact")
    packet.setdefault("research_confidence", "high")
    packet.setdefault("stable_locator", "Reviewed fixture transcript, page 1")
    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_SEMANTIC_GATE", gate)
    monkeypatch.setattr(bot, "HISTORICAL_CONTEXT_RESEARCH_DIR", tmp_path / "research")
    monkeypatch.setattr(bot, "HISTORICAL_CONTEXT_REPLY_HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(bot, "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE", tmp_path / "context-receipt.json")
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE",
        tmp_path / "context-outbox.json",
    )
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON", None)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(
        context_formatter,
        "load_and_validate_corpus",
        lambda *_args, **_kwargs: ({packet["quote_id"]: packet}, set()),
    )
    monkeypatch.setattr(
        context_formatter,
        "packet_for_posted_quote",
        lambda *_args, **_kwargs: packet,
    )
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda event, **fields: events.append({"event": event, **fields}),
    )
    return events


def _forbid(operation: str):
    """Return a callback that rejects an unintended live operation."""

    def forbidden(*_args, **_kwargs):
        pytest.fail(f"test attempted forbidden live operation: {operation}")

    return forbidden


def _bind_context_attempt_to_source_receipt(
    store,
    *,
    parent_id: str,
    outbox_attempt: int,
    source_receipt: dict,
) -> str:
    """Bind one claimed outbox attempt to exact canonical source bytes."""

    source_sha256 = hashlib.sha256(
        context_formatter.canonical_json_bytes(source_receipt)
    ).hexdigest()
    store.bind_attempt_source_receipt(
        parent_id,
        attempt_number=outbox_attempt,
        source_receipt_sha256=source_sha256,
        source_receipt_attempt_number=int(source_receipt["attempt_number"]),
    )
    return source_sha256


@pytest.fixture(autouse=True)
def isolated_incident_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Isolate incident paths and forbid unscoped writes for one test."""

    paths = {
        "BASE_DIR": tmp_path,
        "LINES_FILE": tmp_path / "quotes.txt",
        "LINES_USED_FILE": tmp_path / "lines_used.json",
        "IMAGES_USED_FILE": tmp_path / "images_used.json",
        "STATE_FILE": tmp_path / "bot_state.json",
        "REGULAR_POST_RECEIPT_FILE": tmp_path / "regular_post_receipt.json",
        "MEME_POST_RECEIPT_FILE": tmp_path / "meme_post_receipt.json",
        "HISTORICAL_CONTEXT_REPLY_HISTORY_FILE": tmp_path / "context_history.json",
        "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE": (
            tmp_path / "historical_context_reply_receipt.json"
        ),
        "HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE": tmp_path / "context_outbox.json",
        "CONFIRMED_REPLY_RECEIPT_FILE": tmp_path / "confirmed_reply_receipt.json",
        "AMBIGUOUS_POST_OUTCOME_FILE": tmp_path / "ambiguous_post_outcome.json",
        "AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE": (
            tmp_path / "ambiguous_post_outcome.restart_barrier.json"
        ),
        "REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE": (
            tmp_path / REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_BASENAME
        ),
        "CONTROL_FILE": tmp_path / "control.json",
        "COMPLETED_QUOTE_RESEARCH_FILE": tmp_path / "research_packets.json",
        "HISTORICAL_CONTEXT_RESEARCH_DIR": tmp_path / "research",
        "MEME_DIR": tmp_path / "memes",
        "MEME_ANALYSIS_FILE": tmp_path / "meme_analysis.json",
    }
    for name, value in paths.items():
        monkeypatch.setattr(bot, name, value)
    create_test_protocol_activation(
        bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE
    )

    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    monkeypatch.setattr(bot, "_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN", False)
    monkeypatch.setattr(bot, "_RETAINED_CONFIRMED_POST_SIGINT_GUARD", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_SEMANTIC_GATE", None)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": False},
    )
    monkeypatch.setattr(
        bot,
        "_CONTROL_CACHE",
        {
            "signature": None,
            "data": {},
            "has_valid": False,
            "failure_signature": None,
        },
    )

    monkeypatch.setattr(bot, "x_request", _forbid("X request"))
    monkeypatch.setattr(bot.requests, "request", _forbid("requests.request"))
    monkeypatch.setattr(bot, "create_post", _forbid("X post creation"))
    monkeypatch.setattr(bot, "upload_media", _forbid("X media upload"))
    monkeypatch.setattr(bot, "save_state", _forbid("unscoped bot state write"))
    monkeypatch.setattr(
        bot,
        "save_quote_used_hashes",
        _forbid("unscoped quote-history write"),
    )
    monkeypatch.setattr(
        bot,
        "save_image_used_basenames",
        _forbid("unscoped image-history write"),
    )
    monkeypatch.setattr(bot, "cache_tweet", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bot,
        "record_recent_own_post",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        bot,
        "maybe_schedule_meme_after_quote_post",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

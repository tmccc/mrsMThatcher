"""Build local reply evidence, drafts, receipts and isolated reply-cycle fixtures."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import re
from datetime import datetime
from unittest.mock import Mock

import pytest

from mrs_bot_reply_cycle_interfaces import PreparedReplyContext
from tests.helpers.bot_runtime import SCENARIOS, bot
from tests.fake_api_server import load_scenario
from tests.helpers.mention_fixtures import editorial_no_reply
from tests.helpers.reply_evaluation import legacy_reply_evaluator
from tests.helpers.single_call_fixtures import FakeHttpResponse
from reply_evidence import EvidencePassage
from single_call_reply import (
    STRATEGY_VERSION,
    ValidatedReply,
    build_model_payload,
    create_durable_draft,
)


class UnitReplyEvidenceRepository:
    """Exact local evidence fixture accepted by draft revalidation."""

    def __init__(self) -> None:
        passage = EvidencePassage(
            evidence_id="a" * 64,
            source_hash="b" * 64,
            quote_id="c" * 64,
            field="historical_context",
            passage="People moved from East Germany towards West Germany in November 1989.",
            source_title="Unit source",
            source_url="https://example.invalid/unit",
            stable_locator="unit:1",
            verification_status="exact",
            research_confidence="high",
            trusted_fact_eligible=True,
        )
        self.passage = passage
        self.passages = {passage.evidence_id: passage}

    def detected_authorised_quote_ids(self, _text: str) -> set[str]:
        return set()

    def detected_authorised_quote_ids_outside_exact(
        self,
        _text: str,
        _exact_text: str,
    ) -> set[str]:
        return set()

    def exact_quote_is_authorised(self, _text: str) -> bool:
        return False

    def resolve_context_quotation(self, _context: dict[str, object]) -> None:
        return None

    def candidate_passages(
        self,
        _query: str,
        *,
        maximum_packets: int,
        maximum_passages: int,
        preferred_quote_id: str | None,
        trusted_only: bool = False,
    ) -> list[EvidencePassage]:
        """Return the one trusted unit passage within requested bounds."""

        assert maximum_packets == 8
        assert maximum_passages == 32
        assert preferred_quote_id is None
        assert trusted_only is True
        return [self.passage]


UNIT_REPLY_REPOSITORY = UnitReplyEvidenceRepository()


def unit_reply_context(
    *,
    target_id: str = "100",
    thread_id: str | None = None,
    lane: str = "mention",
    contribution: str = "A contribution.",
    clarification_request: dict[str, str] | None = None,
    target_author_id: str = "200",
) -> dict[str, object]:
    """Return a minimal canonical production reply context."""

    root_id = thread_id or target_id
    target_turn = {
        "post_id": target_id,
        "author_role": "user",
        "text": contribution,
    }
    return {
        "target_id": target_id,
        "thread_id": root_id,
        "root_post_id": root_id,
        "parent_post_id": None,
        "lane": lane,
        "incoming_contribution": contribution,
        "quoted_post": None,
        "parent_thread": [],
        "visible_conversation": [target_turn],
        "visual_description": None,
        "clarification_request": clarification_request,
        "current_date": "2026-07-20",
        "target_author_id": target_author_id,
        "target_created_at": "2026-07-20T12:00:00Z",
    }


def unit_approved_reply(
    context: dict[str, object],
    *,
    text: str = "Thank you for the observation.",
    mode: str = "courtesy",
    factual: bool = False,
) -> ValidatedReply:
    """Return one locally validated single-call reply and durable draft."""

    # This fixture declares facts explicitly; it does not classify arbitrary
    # prose or stand in for a model's editorial judgement.
    factual_sentences = (
        [part.strip() for part in re.split(r'(?<=[.!?])\s+', text) if part.strip()]
        if factual or mode == "direct_factual_answer" else []
    )
    fixture_repository = UnitReplyEvidenceRepository()
    fixture_passages = [
        dataclasses.replace(fixture_repository.passage,
                            evidence_id=hashlib.sha256(sentence.encode()).hexdigest(),
                            passage=sentence)
        for sentence in factual_sentences
    ]
    if fixture_passages:
        fixture_repository.candidate_passages = lambda *_args, **_kwargs: fixture_passages
        for passage in fixture_passages:
            UNIT_REPLY_REPOSITORY.passages[passage.evidence_id] = passage
    payload, fact_map = build_model_payload(
        context=context,
        repository=fixture_repository,
    )
    reply_kind = (
        "direct_factual"
        if factual or mode == "direct_factual_answer"
        else "social" if mode == "courtesy" else "principle"
    )
    used_fact_ids = [f"F{index}" for index in range(1, len(factual_sentences) + 1)]
    output = {
        "decision": "reply",
        "reply_kind": reply_kind,
        "reply": text,
        "used_fact_ids": used_fact_ids,
        "factual_claims": [{"text": sentence, "fact_ids": [fact_id]} for sentence, fact_id in zip(factual_sentences, used_fact_ids)],
        "reason_code": "useful_reply",
    }
    draft = create_durable_draft(
        output=output,
        payload=payload,
        fact_map=fact_map,
        images=[],
        target_author_id=str(context["target_author_id"]),
    )
    metadata = {
        "strategy_version": STRATEGY_VERSION,
        "reply_kind": reply_kind,
        "reason_code": "useful_reply",
        "used_fact_ids": used_fact_ids,
        "used_fact_count": len(used_fact_ids),
        "trusted_fact_count": len(payload["trusted_facts"]),
        "model_call_count": 1,
        "validated_draft_hash": draft["validated_draft_hash"],
    }
    return ValidatedReply(text, draft, metadata)


def unit_confirmed_reply_receipt(
    *,
    target_id: str = "100",
    reply_post_id: str = "999",
    author_id: str = "200",
    lane: str = "mention",
    contribution: str = "A contribution.",
    text: str = "Thank you for the observation.",
    epoch: int = 2_000_000_000,
    factual: bool = False,
    clarification_request: dict[str, str] | None = None,
    conversation_id: str | None = None,
    original_post_id: str | None = None,
) -> dict[str, object]:
    thread_id = conversation_id or target_id
    resolved_original_post_id = original_post_id or "900"
    context = unit_reply_context(
        target_id=target_id,
        thread_id=thread_id,
        lane=lane,
        contribution=contribution,
        clarification_request=clarification_request,
        target_author_id=author_id,
    )
    if lane == "quote_tweet":
        original_turn = {
            "post_id": resolved_original_post_id,
            "author_role": "account",
            "text": "Original account post.",
        }
        target_turn = copy.deepcopy(context["visible_conversation"][-1])
        context.update(
            {
                "root_post_id": resolved_original_post_id,
                "parent_post_id": resolved_original_post_id,
                "quoted_post": copy.deepcopy(original_turn),
                "parent_thread": [copy.deepcopy(original_turn)],
                "visible_conversation": [original_turn, target_turn],
            }
        )
    reply = unit_approved_reply(
        context,
        text=text,
        mode="direct_factual_answer" if factual else "courtesy",
        factual=factual,
    )
    receipt: dict[str, object] = {
        "schema_version": 2,
        "target_id": target_id,
        "reply_post_id": reply_post_id,
        "author_id": author_id,
        "reply_epoch": epoch,
        "daily_reply_date": bot.epoch_date_str(epoch),
        "candidate_source": lane,
        "conversation_id": thread_id,
        "reply_text": text,
        "reply_context": context,
        "ai_reply_draft": reply.draft_record,
    }
    if lane == "quote_tweet":
        receipt["daily_quote_reply_date"] = bot.epoch_date_str(epoch)
        receipt["original_post_id"] = resolved_original_post_id
    return receipt


def unit_sending_reply_receipt(**kwargs: object) -> dict[str, object]:
    """Build the schema-v3 pre-send form of a unit reply receipt."""
    receipt = unit_confirmed_reply_receipt(**kwargs)
    receipt["schema_version"] = 3
    receipt["lifecycle_state"] = "sending"
    receipt.pop("reply_post_id")
    return receipt


def unit_historical_context_sending_receipt(
    *,
    text: str = "Context",
    parent_post_id: str = "111",
) -> dict[str, object]:
    """Build the compact durable owner for low-level create_post tests."""

    return {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": parent_post_id,
        "quote_id": "a" * 64,
        "reply_text": text,
        "reply_epoch": 2_000_000_000,
        "started_at": "2026-07-31T12:00:00Z",
        "attempt_number": 1,
    }


def prepare_unit_historical_context_create(
    *,
    text: str = "Context",
    parent_post_id: str = "111",
) -> dict[str, object]:
    """Persist and return one exact historical-context sending receipt."""

    receipt = unit_historical_context_sending_receipt(
        text=text,
        parent_post_id=parent_post_id,
    )
    bot.atomic_write_json(bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE, receipt)
    return receipt


def unit_confirmed_v3_reply_receipt(**kwargs: object) -> dict[str, object]:
    """Build the schema-v3 confirmed form of a unit reply receipt."""
    receipt = unit_sending_reply_receipt(**kwargs)
    receipt["lifecycle_state"] = "confirmed"
    receipt["reply_post_id"] = str(kwargs.get("reply_post_id", "999"))
    return receipt


def unit_v4_reply_receipt_template(**kwargs: object) -> dict[str, object]:
    """Build the untimed schema-v4 template used immediately before sending."""
    receipt = unit_sending_reply_receipt(**kwargs)
    receipt["schema_version"] = 4
    for field in (
        "attempt_epoch",
        "confirmation_epoch",
        "reply_epoch",
        "daily_reply_date",
        "daily_quote_reply_date",
    ):
        receipt.pop(field, None)
    return receipt


def unit_sending_v4_reply_receipt(
    *,
    attempt_epoch: int = 2_000_000_000,
    **kwargs: object,
) -> dict[str, object]:
    """Build a complete schema-v4 sending receipt with an attempt time."""
    receipt = unit_v4_reply_receipt_template(**kwargs)
    attempt_date = bot.epoch_date_str(attempt_epoch)
    receipt["attempt_epoch"] = attempt_epoch
    receipt["reply_epoch"] = attempt_epoch
    receipt["daily_reply_date"] = attempt_date
    if receipt["candidate_source"] == "quote_tweet":
        receipt["daily_quote_reply_date"] = attempt_date
    return receipt


def unit_confirmed_v4_reply_receipt(
    *,
    attempt_epoch: int = 2_000_000_000,
    confirmation_epoch: int = 2_000_000_005,
    **kwargs: object,
) -> dict[str, object]:
    """Build a schema-v4 confirmed receipt with authoritative confirmation time."""
    receipt = unit_sending_v4_reply_receipt(
        attempt_epoch=attempt_epoch,
        **kwargs,
    )
    confirmation_date = bot.epoch_date_str(confirmation_epoch)
    receipt["lifecycle_state"] = "confirmed"
    receipt["reply_post_id"] = str(kwargs.get("reply_post_id", "999"))
    receipt["confirmation_epoch"] = confirmation_epoch
    receipt["reply_epoch"] = confirmation_epoch
    receipt["daily_reply_date"] = confirmation_date
    if receipt["candidate_source"] == "quote_tweet":
        receipt["daily_quote_reply_date"] = confirmation_date
    return receipt


def reply_evaluation_record(target_id: str, evaluated_epoch: int) -> dict:
    return {
        "target_id": target_id,
        "lane": "mention",
        "outcome": "no_reply",
        "reason": "terminal",
        "evaluated_epoch": evaluated_epoch,
    }


def configure_normal_cycle(monkeypatch):
    """Configure a deterministic normal-reply cycle with no unexpected provider access."""
    epoch = 2_000_000_000
    monkeypatch.setattr(bot, "now_epoch", lambda: epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(epoch))
    monkeypatch.setattr(bot, "single_call_reply", {**bot.single_call_reply, "enabled": True})
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 48)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 6)
    monkeypatch.setattr(bot, "MAX_MENTIONS_PER_CHECK", 5)
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", Mock(return_value=[]))
    monkeypatch.setattr(bot, "x_request", Mock(side_effect=AssertionError("unexpected provider request")))
    monkeypatch.setattr(bot, "create_post", Mock(side_effect=AssertionError("unexpected remote write")))
    monkeypatch.setattr(
        bot, "build_context_for_reply_ai",
        lambda candidate, _state: PreparedReplyContext(
            unit_reply_context(
                target_id=candidate["id"], contribution=candidate["text"],
                target_author_id=candidate["author_id"],
            ),
            {},
        ),
    )
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", Mock(return_value={}))
    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(Mock(side_effect=editorial_no_reply)))


def configure_quote_cycle(monkeypatch):
    """Configure one quote-reply scenario with no unexpected provider access."""
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    original = scenario["tweets"]["900"]
    quotes = scenario["quote_tweets"]["900"]["data"]
    epoch = 2_000_000_000
    monkeypatch.setattr(bot, "now_epoch", lambda: epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(epoch))
    monkeypatch.setattr(bot, "single_call_reply", {**bot.single_call_reply, "enabled": True})
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "build_quote_lookup_post_ids", Mock(return_value=["900"]))
    monkeypatch.setattr(bot, "get_tweet_by_id_cached", Mock(return_value=original))
    monkeypatch.setattr(bot, "get_quote_tweets_for_posts", Mock(return_value={"900": quotes}))
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", Mock(return_value={}))
    monkeypatch.setattr(bot, "x_request", Mock(side_effect=AssertionError("unexpected provider request")))
    monkeypatch.setattr(bot, "create_post", Mock(side_effect=AssertionError("unexpected remote write")))
    return original, quotes


@pytest.fixture
def image_case():
    """Return a fresh HTTP image response and its bound candidate media context."""
    response = FakeHttpResponse(200, headers={"Content-Type": "image/png"})
    media = bot.reply_media_context_for_candidate(
        {"id": "target", "_attached_media": [{
            "media_key": "native-photo", "type": "photo",
            "url": "http://127.0.0.1/media/native.png",
        }]},
        lane="mention", target_id="target",
    )
    return response, media

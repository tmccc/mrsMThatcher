from __future__ import annotations

import copy
import io
import json
import hashlib
import logging
import multiprocessing
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from tests.helpers.protocol_activation import create_test_protocol_activation

UNIT_BASE_DIRECTORY = tempfile.TemporaryDirectory(
    prefix=f"mrsMThatcher-unit-import-{os.getpid()}-"
)
UNIT_BASE = Path(UNIT_BASE_DIRECTORY.name)

IMPORT_ENV = {
    "MRS_TEST_MODE": "1",
    "MRS_BASE_DIR": str(UNIT_BASE),
    "MRS_LOG_FILE": str(UNIT_BASE / "unit-test.log"),
    "X_API_BASE_URL": "http://127.0.0.1:9",
    "X_UPLOAD_BASE_URL": "http://127.0.0.1:9",
    "XAI_API_BASE_URL": "http://127.0.0.1:9/v1",
    "X_CONSUMER_KEY": "dummy",
    "X_CONSUMER_SECRET": "dummy",
    "X_ACCESS_TOKEN": "dummy",
    "X_ACCESS_SECRET": "dummy",
    "X_MY_USER_ID": "12345",
    "XAI_API_KEY": "dummy",
    "X_BEARER_TOKEN": "dummy",
}
ORIGINAL_ENV = {key: os.environ.get(key) for key in IMPORT_ENV}
os.environ.update(IMPORT_ENV)

import mrsMThatcher2 as bot  # noqa: E402
import exact_receipt_retirement as exact_retirement_module  # noqa: E402
import remote_write_transport_journal as transport_journal_module  # noqa: E402

SOURCE_DEFAULT_AI_FIRST_REPLY_STRATEGY = copy.deepcopy(bot.ai_first_reply_strategy)
bot.ai_first_reply_strategy = {
    **bot.ai_first_reply_strategy,
    "enabled": True,
}

for key, value in ORIGINAL_ENV.items():
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value

from tests.fake_api_server import FakeApiServer, load_scenario  # noqa: E402
from reply_evidence import EvidencePassage  # noqa: E402
from reply_strategy import (  # noqa: E402
    AIReply,
    CLAIM_AUDITED_MODES,
    STRATEGY_VERSION,
    build_draft_record,
    split_reply_sentences,
)

SCENARIOS = Path(__file__).resolve().parent / "fixtures" / "scenarios"


def test_valid_receipt_epoch_uses_fixed_transaction_policy() -> None:
    assert bot.valid_receipt_epoch(1_499_999_999) is False
    assert bot.valid_receipt_epoch(1_500_000_000) is True
    assert bot.valid_receipt_epoch(4_102_444_800) is True
    assert bot.valid_receipt_epoch(4_102_444_801) is False


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


UNIT_REPLY_REPOSITORY = UnitReplyEvidenceRepository()


def unit_reply_context(
    *,
    target_id: str = "100",
    thread_id: str | None = None,
    lane: str = "mention",
    contribution: str = "A contribution.",
    clarification_request: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "target_id": target_id,
        "thread_id": thread_id or target_id,
        "lane": lane,
        "incoming_contribution": contribution,
        "quoted_post": None,
        "parent_thread": [],
        "clarification_request": clarification_request,
        "current_date": "2026-07-20",
    }


def unit_approved_reply(
    context: dict[str, object],
    *,
    text: str = "Thank you for the observation.",
    mode: str = "courtesy",
    factual: bool = False,
) -> AIReply:
    claims: list[dict[str, object]] = []
    evidence: list[dict[str, object]] = []
    sentences = split_reply_sentences(text)
    if factual:
        for index, sentence in enumerate(sentences, start=1):
            claim_record = {
                "claim_id": f"claim-{index}",
                "claim_text": sentence,
                "requires_evidence": True,
                "actor": "people in East Germany",
                "action_or_relationship": "moved",
                "direction_or_polarity": "East to West",
                "date_or_period": "November 1989",
                "quantity": "",
            }
            claims.append(claim_record)
            evidence.append({
                "claim_id": claim_record["claim_id"],
                "claim_text": sentence,
                "verdict": "supports",
                "evidence": [{
                    "evidence_id": UNIT_REPLY_REPOSITORY.passage.evidence_id,
                    "exact_supporting_passage": UNIT_REPLY_REPOSITORY.passage.passage,
                    "relation": "supports",
                    "source_hash": UNIT_REPLY_REPOSITORY.passage.source_hash,
                    "evidence_input_hash": UNIT_REPLY_REPOSITORY.passage.model_input_hash(),
                }],
                "actor": claim_record["actor"],
                "action_or_relationship": claim_record["action_or_relationship"],
                "direction_or_polarity": claim_record["direction_or_polarity"],
                "date_or_period": claim_record["date_or_period"],
                "quantity": "",
                "explanation": "Exact unit evidence supports the claim.",
            })
    proposal = {
        "mode": mode,
        "interpretation": "Unit-test interpretation.",
        "proposed_reply": text,
        "direct_factual_question_present": mode == "direct_factual_answer",
        "requested_answer_type": "action" if mode == "direct_factual_answer" else "none",
        "direct_answer_text": sentences[0] if mode == "direct_factual_answer" else "",
        "factual_claims": claims,
        "exact_thatcher_wording_used": False,
        "exact_thatcher_wording": "",
        "tone": "neutral",
        "confidence": "high",
        "no_reply_reason": "",
    }
    review = {
        "verdict": "approve",
        "reasons": [],
        "sentence_assessments": [{
            "sentence_text": sentence,
            "classification": "factual_claim" if factual else "other_non_factual",
            "factual_claims": [sentence] if factual else [],
            "non_factual_basis": "none" if factual else "rhetorical_question",
            "world_claim_checks": {
                "asserts_actor_state_or_action": factual,
                "asserts_causal_or_predictive_relation": False,
                "asserts_comparison_or_outcome": False,
                "asserts_historical_date_or_quantity": False,
                "asserts_meaning_or_attribution": False,
                "purely_non_factual": not factual,
            },
        } for sentence in sentences],
    }
    claim_audit = None
    if not factual and mode in CLAIM_AUDITED_MODES:
        claim_audit = {
            "actual_factual_claims": [],
            "sentence_assessments": [{
                "sentence_text": sentence,
                "factual_claims": [],
                "world_claim_checks": {
                    "asserts_actor_state_or_action": False,
                    "asserts_causal_or_predictive_relation": False,
                    "asserts_comparison_or_outcome": False,
                    "asserts_historical_date_or_quantity": False,
                    "asserts_meaning_or_attribution": False,
                    "purely_non_factual": True,
                },
            } for sentence in sentences],
        }
    draft = build_draft_record(
        context=context,
        proposer=proposal,
        evidence=evidence,
        reviewer=review,
        claim_auditor=claim_audit,
        resolved_quotation=None,
        config=bot.ai_first_reply_strategy,
        model_call_count=3 if factual or claim_audit is not None else 2,
        revision_count=0,
        creation_time="2026-07-20T12:00:00Z",
        retrieved_count=1 if factual else 0,
    )
    metadata = {
        "strategy_version": STRATEGY_VERSION,
        "mode": mode,
        "tone": "neutral",
        "confidence": "high",
        "factual_claim_count": len(claims),
        "evidence_ids": draft["evidence_ids"],
        "reviewer_verdict": "approve",
        "model_call_count": draft["model_call_count"],
        "revision_count": 0,
    }
    return AIReply(text, draft, metadata)


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
    )
    if lane == "quote_tweet":
        context["quoted_post"] = {
            "post_id": resolved_original_post_id,
            "author_role": "account",
            "text": "Original account post.",
        }
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


def test_schema_v4_source_lineage_accepts_real_ai_reply_string_subclass() -> None:
    sending = unit_sending_v4_reply_receipt()
    reply = unit_approved_reply(
        sending["reply_context"],
        text=str(sending["reply_text"]),
    )
    sending["reply_text"] = reply
    sending["ai_reply_draft"] = reply.draft_record
    assert bot.sending_reply_receipt_is_semantically_valid(sending)

    confirmed = bot._confirmed_reply_receipt_from_sending(
        sending,
        reply_post_id="999",
        confirmation_epoch=2_000_000_005,
    )

    assert bot.confirmed_reply_receipt_is_semantically_valid(confirmed)
    reconstructed = bot.conversational_sending_receipt_from_confirmed(confirmed)
    assert reconstructed["reply_text"] is reply
    assert reconstructed == sending


@pytest.mark.parametrize("schema_version", (4.0, True))
def test_conversational_source_lineage_helpers_require_integer_schema(
    schema_version: object,
) -> None:
    sending = unit_sending_v4_reply_receipt()
    confirmed = bot._confirmed_reply_receipt_from_sending(
        sending,
        reply_post_id="999",
        confirmation_epoch=2_000_000_005,
    )
    confirmed["schema_version"] = schema_version
    with pytest.raises(ValueError, match="exact source lineage"):
        bot.conversational_sending_receipt_from_confirmed(confirmed)

    template = unit_v4_reply_receipt_template()
    template["schema_version"] = schema_version
    with pytest.raises(RuntimeError, match="schema-v4 sending template"):
        bot.bind_conversational_reply_attempt_time(template)


@pytest.fixture(autouse=True)
def isolate_regular_post_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Operational command tests model the supported post-bootstrap dispatch path.
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", tmp_path / "regular_post_receipt.json")
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", tmp_path / "meme_post_receipt.json")
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_HISTORY_FILE",
        tmp_path / "historical_context_reply_history.json",
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE",
        tmp_path / "historical_context_reply_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE",
        tmp_path / "historical_context_reply_outbox.json",
    )
    monkeypatch.setattr(bot, "CONFIRMED_REPLY_RECEIPT_FILE", tmp_path / "confirmed_reply_receipt.json")
    monkeypatch.setattr(bot, "AMBIGUOUS_POST_OUTCOME_FILE", tmp_path / "ambiguous_post_outcome.json")
    monkeypatch.setattr(
        bot,
        "AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE",
        tmp_path / "ambiguous_post_outcome.restart_barrier.json",
    )
    monkeypatch.setattr(
        bot,
        "REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE",
        tmp_path / bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_BASENAME,
    )
    create_test_protocol_activation(
        bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE
    )
    monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
    monkeypatch.setattr(
        bot,
        "_CONTROL_CACHE",
        {"signature": None, "data": {}, "has_valid": False, "failure_signature": None},
    )
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    monkeypatch.setattr(bot, "_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN", False)
    monkeypatch.setattr(bot, "_RETAINED_CONFIRMED_POST_SIGINT_GUARD", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON", None)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON", None)
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_SEMANTIC_GATE",
        SimpleNamespace(
            available=True,
            ledger_sha256="unit-test-ledger",
            projection_sha256="unit-test-projection",
            disposition=lambda _quote_id: None,
            reviewed_disposition=lambda _quote_id: None,
        ),
    )
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    monkeypatch.setattr(
        bot,
        "completed_research_quote_hashes",
        lambda: {
            bot.quote_text_hash(line)
            for line in Path(bot.LINES_FILE).read_text(encoding="utf-8").splitlines()
            if line.strip()
        },
    )


def quote_analysis_for_lines(lines: list[str], analyses: dict[int, dict] | None = None) -> dict:
    analyses = analyses or {}
    items: dict[str, dict] = {}
    line_index: dict[str, str] = {}
    for idx, text in enumerate(lines):
        if not text.strip():
            continue
        quote_hash = bot.quote_text_hash(text)
        line_index[str(idx + 1)] = quote_hash
        items.setdefault(
            quote_hash,
            {
                "text": bot.collapse_quote_whitespace(text),
                "quote_hash": quote_hash,
                "line_numbers": [idx + 1],
                "analysis": analyses.get(idx, {"seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"}}),
            },
        )
    return {
        "analysis_kind": "quotes",
        "schema_version": 2,
        "source": {"source_sha256": hashlib.sha256(("".join(line + "\n" for line in lines)).encode("utf-8")).hexdigest()},
        "line_index": line_index,
        "items": items,
    }


def xai_user_content(server: FakeApiServer) -> str | list[dict]:
    assert server.xai_requests
    return server.xai_requests[-1]["messages"][1]["content"]


def xai_image_urls(server: FakeApiServer) -> list[str]:
    content = xai_user_content(server)
    if not isinstance(content, list):
        return []
    return [
        part["image_url"]["url"]
        for part in content
        if part.get("type") == "image_url"
    ]


def fake_xai_response(status_code: int, body: dict | str) -> bot.requests.Response:
    response = bot.requests.Response()
    response.status_code = status_code
    if isinstance(body, str):
        response._content = body.encode("utf-8")
    else:
        response._content = json.dumps(body).encode("utf-8")
        response.headers["Content-Type"] = "application/json"
    return response


def invalid_pagination_cursor_error() -> bot.ApiError:
    """Return a representative X invalid-pagination-token response."""
    return bot.ApiError(
        (
            'X API error 400: {"errors":[{"parameters":'
            '{"pagination_token":["expired-token"]},'
            '"message":"The `pagination_token` query parameter value '
            '[expired-token] is not valid"}]}'
        ),
        service="x",
        status_code=400,
    )


def run_native_photo_mention_with_xai_responses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    responses: list[dict],
) -> tuple[str, list[dict], list[dict], dict]:
    scenario = {
        "mentions": [
            {
                "id": "100",
                "text": "This depends on the image. https://t.co/example",
                "author_id": "200",
                "conversation_id": "100",
                "created_at": "2026-07-06T10:00:00Z",
                "attachments": {"media_keys": ["3_100"]},
            }
        ],
        "mentions_extra": {
            "includes": {
                "media": [
                    {
                        "media_key": "3_100",
                        "type": "photo",
                        "url": "https://pbs.twimg.com/media/native-photo.jpg",
                    }
                ]
            }
        },
        "xai_responses": responses,
    }
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        enabled_strategy = copy.deepcopy(bot.ai_first_reply_strategy)
        enabled_strategy["enabled"] = True
        monkeypatch.setattr(bot, "ai_first_reply_strategy", enabled_strategy)
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")
        status = bot.maybe_reply_to_mentions(state)
        xai_posts = [request for request in server.requests if request["path"] == "/v1/chat/completions"]
        return status, xai_posts, list(server.xai_requests), state
    finally:
        server.stop()


def image_analysis_for_paths(paths: list[Path], analyses: dict[str, dict] | None = None) -> dict:
    analyses = analyses or {}
    path_index: dict[str, str] = {}
    items: dict[str, dict] = {}
    for path in paths:
        image_hash = bot.file_sha256(path)
        path_index[path.name] = image_hash
        items[image_hash] = {
            "paths": [path.name],
            "image_hash": image_hash,
            "analysis": analyses.get(path.name, {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}}),
        }
    return {"analysis_kind": "images", "schema_version": 3, "path_index": path_index, "items": items}


def write_image_analysis(path: Path, analysis: dict) -> None:
    path.write_text(json.dumps(analysis), encoding="utf-8")


def test_used_history_json_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "used.json"

    bot.save_used_set(path, {3, 1, 2, 11})

    assert json.loads(path.read_text(encoding="utf-8")) == [1, 2, 3, 11]
    assert bot.load_used_set(path) == {1, 2, 3, 11}


def test_used_history_refuses_legacy_pickle_when_json_missing(tmp_path: Path) -> None:
    json_path = tmp_path / "used.json"
    pickle_path = tmp_path / "used.pickle"
    pickle_path.write_bytes(b"legacy pickle no longer trusted")

    with pytest.raises(bot.CorruptUsedHistoryError):
        bot.load_used_set(json_path, legacy_pickle_path=pickle_path)
    assert not json_path.exists()


def test_used_history_bad_json_with_legacy_pickle_fails_closed(tmp_path: Path) -> None:
    json_path = tmp_path / "used.json"
    pickle_path = tmp_path / "used.pickle"
    json_path.write_text("{bad json", encoding="utf-8")
    pickle_path.write_bytes(b"legacy pickle no longer trusted")

    with pytest.raises(bot.CorruptUsedHistoryError):
        bot.load_used_set(json_path, legacy_pickle_path=pickle_path)


def test_used_history_json_order_is_normalized_on_load(tmp_path: Path) -> None:
    json_path = tmp_path / "used.json"
    json_path.write_text("[11, 2, 1]", encoding="utf-8")

    assert bot.load_used_set(json_path) == {1, 2, 11}
    assert json.loads(json_path.read_text(encoding="utf-8")) == [1, 2, 11]


def test_used_history_missing_without_legacy_pickle_returns_empty_set(tmp_path: Path) -> None:
    json_path = tmp_path / "missing.json"

    assert bot.load_used_set(json_path) == set()


def test_quote_metadata_uses_current_quote_hash_not_stale_line_index() -> None:
    quote_hash = bot.quote_text_hash("Quote text")
    analysis = {"primary_topics": ["government"]}
    data = {"line_index": {"1": "stale"}, "items": {quote_hash: {"text": "Quote text", "analysis": analysis}}}

    assert bot.quote_metadata_for_hash(data, quote_hash, "Quote text") == analysis


def test_quote_override_deep_merges_only_requested_field() -> None:
    quote_hash = "hash1"
    raw = {
        "items": {
            quote_hash: {
                "text": "Quote text",
                "line_numbers": [175],
                "analysis": {
                    "seasonality": {
                        "hard_exclude_outside_windows": True,
                        "relevance": "strong",
                    },
                    "scores": {"general_post_suitability": 80},
                },
            }
        }
    }
    overrides = {
        "quote_overrides": {
            quote_hash: {
                "expected_line_numbers": [175],
                "expected_text": "Quote text",
                "analysis_patch": {"seasonality": {"hard_exclude_outside_windows": False}},
            }
        }
    }

    merged = bot.apply_quote_analysis_overrides(raw, overrides)

    assert raw["items"][quote_hash]["analysis"]["seasonality"]["hard_exclude_outside_windows"] is True
    assert merged["items"][quote_hash]["analysis"]["seasonality"]["hard_exclude_outside_windows"] is False
    assert merged["items"][quote_hash]["analysis"]["seasonality"]["relevance"] == "strong"
    assert merged["items"][quote_hash]["analysis"]["scores"]["general_post_suitability"] == 80


def test_stale_quote_override_is_warned_and_skipped(caplog: pytest.LogCaptureFixture) -> None:
    quote_hash = "hash1"
    raw = {
        "items": {
            quote_hash: {
                "text": "Current text",
                "line_numbers": [175],
                "analysis": {"seasonality": {"hard_exclude_outside_windows": True}},
            }
        }
    }
    overrides = {
        "quote_overrides": {
            quote_hash: {
                "expected_line_numbers": [175],
                "expected_text": "Old text",
                "analysis_patch": {"seasonality": {"hard_exclude_outside_windows": False}},
            }
        }
    }

    merged = bot.apply_quote_analysis_overrides(raw, overrides)

    assert merged["items"][quote_hash]["analysis"]["seasonality"]["hard_exclude_outside_windows"] is True
    assert "expected_text does not match" in caplog.text


def test_mm_dd_window_matching_supports_normal_and_cross_year_windows() -> None:
    assert bot.mm_dd_in_window("12-20", "12-10", "12-28")
    assert not bot.mm_dd_in_window("01-05", "12-10", "12-28")
    assert bot.mm_dd_in_window("01-05", "12-01", "02-28")
    assert bot.mm_dd_in_window("12-20", "12-01", "02-28")
    assert not bot.mm_dd_in_window("03-01", "12-01", "02-28")


def test_hard_seasonal_exclusion_outside_window() -> None:
    analysis = {
        "seasonality": {
            "hard_exclude_outside_windows": True,
            "relevance": "strong",
            "preferred_windows": [{"start_mm_dd": "12-10", "end_mm_dd": "12-28"}],
        }
    }

    status = bot.quote_season_status(analysis, today_mm_dd="07-05")

    assert status["hard_excluded"] is True
    assert bot.quote_candidate_weight(analysis, today_mm_dd="07-05")[0] == 0


def test_quote_cycle_seasonal_exhaustion_resets_without_selecting_hard_excluded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Normal quote.\nChristmas quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    normal_hash = bot.quote_text_hash("Normal quote.")
    christmas_analysis = {
        "seasonality": {
            "hard_exclude_outside_windows": True,
            "preferred_windows": [{"start_mm_dd": "12-10", "end_mm_dd": "12-28"}],
            "relevance": "strong",
        }
    }
    monkeypatch.setattr(
        bot,
        "load_quote_analysis",
        lambda: quote_analysis_for_lines(["Normal quote.", "Christmas quote."], {1: christmas_analysis}),
    )

    used = {normal_hash}
    chosen = bot.choose_unused_line_candidate(used)

    assert chosen["line_no"] == 0
    assert used == set()


def test_quote_cycle_resets_when_only_unused_quote_is_unanalysed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = ["Analysed quote.", "New unanalysed quote."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    analysed = quote_analysis_for_lines(["Analysed quote."])
    analysed_hash = bot.quote_text_hash("Analysed quote.")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: analysed)

    chosen = bot.choose_unused_line_candidate({analysed_hash})

    assert chosen["text"] == "Analysed quote."


def test_quote_cycle_resets_when_remaining_unused_quotes_are_unanalysed_and_hard_seasonal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = ["Analysed quote.", "Edited unanalysed quote.", "Christmas quote."]
    christmas = {
        "seasonality": {
            "hard_exclude_outside_windows": True,
            "preferred_windows": [{"start_mm_dd": "12-10", "end_mm_dd": "12-28"}],
            "relevance": "strong",
        }
    }
    analysed = quote_analysis_for_lines(["Analysed quote.", "Christmas quote."], {1: christmas})
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: analysed)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))

    chosen = bot.choose_unused_line_candidate({bot.quote_text_hash("Analysed quote.")})

    assert chosen["text"] == "Analysed quote."


def test_strong_in_window_quote_weight_exceeds_ordinary_weight() -> None:
    ordinary = {"seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"}}
    strong = {
        "seasonality": {
            "hard_exclude_outside_windows": False,
            "relevance": "strong",
            "preferred_windows": [{"start_mm_dd": "12-10", "end_mm_dd": "12-28"}],
        }
    }

    assert bot.quote_candidate_weight(strong, today_mm_dd="12-20")[0] > bot.quote_candidate_weight(ordinary, today_mm_dd="12-20")[0]


def test_image_used_history_integer_entries_migrate_to_basenames(tmp_path: Path) -> None:
    paths = [tmp_path / "t01.jpg", tmp_path / "t02.jpg"]
    for path in paths:
        path.write_bytes(path.name.encode("utf-8"))
    images = [str(path) for path in paths]

    migrated, changed = bot.normalise_image_used_basenames({0, 1}, images, image_analysis_for_paths(paths))

    assert migrated == {"t01.jpg", "t02.jpg"}
    assert changed is True


def test_image_used_history_mixed_entries_migrate_and_preserve_missing_basenames(tmp_path: Path) -> None:
    paths = [tmp_path / "t01.jpg", tmp_path / "t02.jpg"]
    for path in paths:
        path.write_bytes(path.name.encode("utf-8"))
    images = [str(path) for path in paths]

    migrated, changed = bot.normalise_image_used_basenames({0, "t02.jpg", "missing.jpg"}, images, image_analysis_for_paths(paths))

    assert migrated == {"t01.jpg", "t02.jpg", "missing.jpg"}
    assert changed is True


def test_currently_eligible_image_cycle_resets_without_marking_seasonal_image_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_paths = []
    for name in ["t01.jpg", "t02.jpg", "t23.jpg"]:
        path = image_dir / name
        path.write_bytes(name.encode("utf-8"))
        image_paths.append(path)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(
        bot,
        "load_image_analysis",
        lambda: image_analysis_for_paths(
            image_paths,
            {
                "t01.jpg": {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}},
                "t02.jpg": {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}},
                "t23.jpg": {
                        "pairing": {},
                        "themes": [],
                        "tone": [],
                        "visual_energy": "low",
                        "quality": {},
                        "seasonality": {
                            "avoid_outside_season_or_occasion": True,
                            "occasions": ["christmas"],
                            "visible_season": "winter",
                        },
                },
            },
        ),
    )

    used = {"t01.jpg", "t02.jpg"}
    chosen = bot.choose_matched_unused_image(used, {"analysis": {"primary_topics": [], "secondary_topics": [], "tone": [], "visual_energy": "low"}}, {})

    assert chosen["basename"] in {"t01.jpg", "t02.jpg"}
    assert "t23.jpg" not in used
    assert used == set()


def test_seasonal_image_re_enters_when_current_date_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_paths = []
    for name in ["t01.jpg", "t23.jpg"]:
        path = image_dir / name
        path.write_bytes(name.encode("utf-8"))
        image_paths.append(path)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 12, 20))
    monkeypatch.setattr(
        bot,
        "load_image_analysis",
        lambda: image_analysis_for_paths(
            image_paths,
            {
                "t01.jpg": {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}},
                "t23.jpg": {
                        "pairing": {"best_for_topics": ["christmas"]},
                        "themes": ["christmas"],
                        "tone": [],
                        "visual_energy": "low",
                        "quality": {},
                        "seasonality": {
                            "avoid_outside_season_or_occasion": True,
                            "occasions": ["christmas"],
                            "visible_season": "winter",
                        },
                },
            },
        ),
    )

    chosen = bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["christmas"], "secondary_topics": [], "tone": [], "visual_energy": "low"}}, {})

    assert chosen["basename"] == "t23.jpg"


def test_generated_image_pool_disabled_keeps_original_image_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    generated = generated_dir / ("tg_" + "a" * 64 + ".png")
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", False)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))

    assert bot.current_image_paths() == [str(original)]


def test_generated_image_pool_merges_separate_analysis_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    quote_hash = "b" * 64
    generated = generated_dir / f"tg_{quote_hash}.png"
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    write_image_analysis(original_analysis_path, image_analysis_for_paths([original]))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths([generated]))

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))

    paths = bot.current_image_paths()
    analysis = bot.load_image_analysis()

    assert paths == [str(original), str(generated)]
    assert set(analysis["path_index"]) == {"t01.jpg", f"tg_{quote_hash}.png"}


def test_generated_image_pool_rejects_paths_outside_generated_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    outside_dir = tmp_path / "outside"
    image_dir.mkdir()
    generated_dir.mkdir()
    outside_dir.mkdir()
    original = image_dir / "t01.jpg"
    outside = outside_dir / ("tg_" + "e" * 64 + ".png")
    original.write_bytes(b"original")
    outside.write_bytes(b"outside")

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "../outside/*.png")

    assert bot.current_image_paths() == [str(original)]


def test_generated_image_pool_fails_closed_on_unclassifiable_basename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    unclassifiable = generated_dir / "reviewed_ai.png"
    original.write_bytes(b"original")
    unclassifiable.write_bytes(b"generated")

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")

    with pytest.raises(RuntimeError, match="unclassifiable"):
        bot.current_image_paths()


def test_generated_image_source_classification() -> None:
    quote_hash = "a" * 64

    assert bot.GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN == 2
    assert bot.image_selection_observability("t44.jpg") == {
        "image_source": "original",
        "origin_quote_hash": None,
        "origin_quote_match": False,
        "origin_quote_boost": 0.0,
    }
    assert bot.image_selection_observability(f"tg_{quote_hash}.png", quote_hash, 4) == {
        "image_source": "generated",
        "origin_quote_hash": quote_hash,
        "origin_quote_match": True,
        "origin_quote_boost": 4.0,
    }
    assert bot.image_selection_observability(f"tg_{quote_hash}.png", "b" * 64, 4) == {
        "image_source": "generated",
        "origin_quote_hash": quote_hash,
        "origin_quote_match": False,
        "origin_quote_boost": 0.0,
    }
    assert bot.generated_image_origin_quote_hash("tg_not-a-real-hash.png") is None
    assert bot.generated_image_origin_quote_hash("tg_" + ("a" * 63) + ".png") is None


def test_generated_image_spacing_helpers_and_state_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    generated = "tg_" + ("a" * 64) + ".png"
    state = {"original_regular_posts_since_generated_image": 0}
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)

    assert bot.generated_images_allowed_by_spacing(state) is False
    bot.update_regular_generated_image_spacing_state(state, "t01.jpg")
    assert state["original_regular_posts_since_generated_image"] == 1
    assert bot.generated_images_allowed_by_spacing(state) is False
    bot.update_regular_generated_image_spacing_state(state, "t02.jpg")
    assert state["original_regular_posts_since_generated_image"] == 2
    assert bot.generated_images_allowed_by_spacing(state) is True
    bot.update_regular_generated_image_spacing_state(state, "t03.jpg")
    assert state["original_regular_posts_since_generated_image"] == 2
    bot.update_regular_generated_image_spacing_state(state, generated)
    assert state["original_regular_posts_since_generated_image"] == 0


@pytest.mark.parametrize("value", [0, 2])
def test_generated_image_spacing_required_accepts_integers(monkeypatch: pytest.MonkeyPatch, value: int) -> None:
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", value)

    assert bot.generated_image_spacing_required() == value


def test_generated_image_spacing_zero_disables_restriction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 0)

    assert bot.generated_images_allowed_by_spacing({"original_regular_posts_since_generated_image": 0}) is True


@pytest.mark.parametrize("bad_value", [True, False, -1, 2.0, 2.5, "2", "x"])
def test_generated_image_spacing_helper_rejects_non_integer_values(
    monkeypatch: pytest.MonkeyPatch,
    bad_value: object,
) -> None:
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", bad_value)

    with pytest.raises(ValueError):
        bot.generated_image_spacing_required()


@pytest.mark.parametrize(
    ("last_image", "expected_count"),
    [
        ("tg_" + ("a" * 64) + ".png", 0),
        ("t01.jpg", 2),
        (None, 2),
    ],
)
def test_legacy_state_initialises_generated_spacing_from_last_regular_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    last_image: str | None,
    expected_count: int,
) -> None:
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
    state = bot.default_state()
    state.pop("original_regular_posts_since_generated_image", None)
    state["last_regular_image_filename"] = last_image

    normalised = bot.normalise_state_candidate(state, path=tmp_path / "state.json")

    assert normalised is not None
    assert normalised["original_regular_posts_since_generated_image"] == expected_count


def test_state_rejects_boolean_generated_spacing_counter(tmp_path: Path) -> None:
    state = bot.default_state()
    state["original_regular_posts_since_generated_image"] = True

    assert bot.normalise_state_candidate(state, path=tmp_path / "state.json") is None


@pytest.mark.parametrize(
    ("key", "bad_value"),
    [
        ("daily_reply_count", 1.9),
        ("last_reply_epoch", 1_800_000_000.5),
        ("daily_reply_count", float("inf")),
    ],
)
def test_state_rejects_fractional_and_non_finite_numbers(
    tmp_path: Path,
    key: str,
    bad_value: float,
) -> None:
    state = bot.default_state()
    state[key] = bad_value

    assert bot.normalise_state_candidate(state, path=tmp_path / "state.json") is None


@pytest.mark.parametrize("value", [0, 2])
def test_runtime_config_validation_accepts_integer_generated_spacing(value: int) -> None:
    assert not bot.validate_runtime_config_values({"GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": value})


@pytest.mark.parametrize("bad_value", [True, False, 2.0, 2.5, "2", "x"])
def test_runtime_config_validation_rejects_non_integer_generated_spacing(bad_value: object) -> None:
    errors = bot.validate_runtime_config_values({"GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": bad_value})
    assert "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN must be an integer" in errors


def test_runtime_config_validation_rejects_negative_generated_spacing() -> None:
    errors = bot.validate_runtime_config_values({"GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": -1})

    assert "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN must be non-negative" in errors


@pytest.mark.parametrize("field", ["fail_closed", "maximum_revisions", "strategy_version"])
def test_ai_first_reply_strategy_validator_rejects_weakened_safety_fields(field: str) -> None:
    strategy = json.loads(json.dumps(bot.ai_first_reply_strategy))
    strategy[field] = {
        "fail_closed": False,
        "maximum_revisions": 2,
        "strategy_version": "legacy",
    }[field]

    errors = bot.validate_runtime_config_values({"ai_first_reply_strategy": strategy})

    assert errors


def test_ai_first_reply_strategy_source_defaults_remain_disabled() -> None:
    example = json.loads(Path("mrsMThatcher.local.example.json").read_text(encoding="utf-8"))

    assert SOURCE_DEFAULT_AI_FIRST_REPLY_STRATEGY["enabled"] is False
    assert example["ai_first_reply_strategy"]["enabled"] is False


def test_disabled_ai_first_strategy_skips_mention_lane_before_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    disabled = copy.deepcopy(bot.ai_first_reply_strategy)
    disabled["enabled"] = False
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ai_first_reply_strategy", disabled)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(
        bot,
        "get_mentions",
        lambda _state: pytest.fail("disabled strategy must not discover mentions"),
    )
    monkeypatch.setattr(
        bot,
        "get_hot_post_reply_candidates",
        lambda _state: pytest.fail("disabled strategy must not discover hot-post replies"),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_DISABLED
    assert state.get("reply_evaluation_records", {}) == {}


def test_disabled_ai_first_strategy_skips_quote_lane_before_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    disabled = copy.deepcopy(bot.ai_first_reply_strategy)
    disabled["enabled"] = False
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
    monkeypatch.setattr(bot, "ai_first_reply_strategy", disabled)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(
        bot,
        "lane_paused",
        lambda *_args, **_kwargs: pytest.fail("disabled strategy must stop before quote-tweet lane work"),
    )

    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_DISABLED
    assert state.get("reply_evaluation_records", {}) == {}


def test_proposer_invalid_pipeline_telemetry_raises_retryable_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import reply_strategy

    enabled = copy.deepcopy(bot.ai_first_reply_strategy)
    enabled["enabled"] = True
    outcome: dict[str, str] = {}
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(bot, "ai_first_reply_strategy", enabled)
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    monkeypatch.setattr(
        reply_strategy,
        "run_reply_pipeline",
        lambda **_kwargs: reply_strategy.PipelineResult(
            None,
            "operational_failure",
            "proposer_invalid",
            2,
            0,
            ({"stage": "proposer", "status": "invalid", "attempt": 2},),
        ),
    )
    monkeypatch.setattr(bot, "log_event", lambda name, **values: events.append((name, values)))

    with pytest.raises(bot.ApiError, match="operational failure: proposer_invalid"):
        bot.generate_ai_first_reply(
            unit_reply_context(),
            evaluation_outcome=outcome,
        )

    assert outcome == {"status": "operational_failure", "reason": "proposer_invalid"}
    assert events == [(
        "ai_reply_pipeline_failure",
        {
            "lane": "mention",
            "target_id": "100",
            "status": "operational_failure",
            "strategy_version": STRATEGY_VERSION,
            "mode": "unavailable",
            "proposer_mode": "not_run",
            "tone": "none",
            "factual_claim_count": None,
            "evidence_ids": [],
            "evidence_confidence": "none",
            "retrieved_count": None,
            "evidence_reference_count": 0,
            "reviewer_verdict": "not_run",
            "terminal_stage": "proposer",
            "claim_auditor_status": "not_run",
            "evidence_status": "not_run",
            "reason": "proposer_invalid",
            "model_call_count": 2,
            "revision_count": 0,
        },
    )]


def test_confirmed_no_reply_pipeline_telemetry_reports_independent_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import reply_strategy

    interpretation_marker = "PRIVATE_PROPOSER_INTERPRETATION_MARKER"
    reason_marker = "PRIVATE_PROPOSER_NO_REPLY_REASON_MARKER"
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    monkeypatch.setattr(
        reply_strategy,
        "run_reply_pipeline",
        lambda **_kwargs: reply_strategy.PipelineResult(
            None,
            "no_reply",
            "independent_no_reply_confirmed",
            2,
            0,
            (
                {
                    "stage": "proposer",
                    "status": "completed",
                    "mode": "no_reply",
                    "tone": "none",
                    "factual_claim_count": 0,
                },
                {
                    "stage": "no_reply_reviewer",
                    "status": "completed",
                    "verdict": "confirm_no_reply",
                },
            ),
        ),
    )
    monkeypatch.setattr(bot, "log_event", lambda name, **values: events.append((name, values)))

    assert bot.generate_ai_first_reply(unit_reply_context()) is None

    name, decision = events[-1]
    assert name == "ai_reply_pipeline_decision"
    assert decision["mode"] == "no_reply"
    assert decision["proposer_mode"] == "no_reply"
    assert decision["terminal_stage"] == "no_reply_reviewer"
    assert decision["reviewer_verdict"] == "confirm_no_reply"
    assert decision["claim_auditor_status"] == "not_run"
    assert decision["evidence_status"] == "not_run"
    assert decision["reason"] == "independent_no_reply_confirmed"
    serialised_decision = json.dumps(decision, sort_keys=True)
    for private_marker in (interpretation_marker, reason_marker):
        assert private_marker not in serialised_decision
    assert "no_reply_reason" not in decision
    assert "interpretation" not in decision


def test_evidence_stage_operational_failure_telemetry_keeps_unknown_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import reply_strategy

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    monkeypatch.setattr(
        reply_strategy,
        "run_reply_pipeline",
        lambda **_kwargs: reply_strategy.PipelineResult(
            None,
            "operational_failure",
            "reviewer_invalid",
            4,
            0,
            (
                {
                    "stage": "proposer",
                    "status": "completed",
                    "mode": "direct_factual_answer",
                    "tone": "neutral",
                    "factual_claim_count": 1,
                },
                {"stage": "evidence", "status": "completed", "supported": True},
                {"stage": "reviewer", "status": "invalid", "attempt": 2},
            ),
        ),
    )
    monkeypatch.setattr(bot, "log_event", lambda name, **values: events.append((name, values)))

    with pytest.raises(bot.ApiError, match="operational failure: reviewer_invalid"):
        bot.generate_ai_first_reply(unit_reply_context())

    name, failure = events[-1]
    assert name == "ai_reply_pipeline_failure"
    assert failure["proposer_mode"] == "direct_factual_answer"
    assert failure["terminal_stage"] == "reviewer"
    assert failure["reviewer_verdict"] == "invalid"
    assert failure["claim_auditor_status"] == "not_run"
    assert failure["evidence_status"] == "completed"
    assert failure["evidence_ids"] is None
    assert failure["evidence_confidence"] == "unavailable"
    assert failure["evidence_reference_count"] is None


def test_approved_ai_reply_decision_logs_existing_evidence_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import reply_strategy

    context = unit_reply_context(
        contribution="Where did people move in November 1989?"
    )
    reply = unit_approved_reply(
        context,
        text=(
            "People moved from East Germany towards West Germany in November "
            "1989."
        ),
        mode="direct_factual_answer",
        factual=True,
    )
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    monkeypatch.setattr(
        reply_strategy,
        "run_reply_pipeline",
        lambda **_kwargs: reply_strategy.PipelineResult(
            reply,
            "approved",
            "reviewer_approved",
            3,
            0,
            (),
        ),
    )
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **values: events.append((name, values)),
    )

    generated = bot.generate_ai_first_reply(context)

    assert generated == reply
    decision = events[-1][1]
    assert decision["evidence_confidence"] == "high"
    assert decision["retrieved_count"] == 1
    assert decision["evidence_reference_count"] == 1


def test_operational_pipeline_failure_does_not_consume_mention_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0
    mention = {
        "id": "100",
        "author_id": "200",
        "text": "@MrsMThatcher A substantive question?",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "conversation_id": "100",
        "referenced_tweets": [],
    }
    enabled = copy.deepcopy(bot.ai_first_reply_strategy)
    enabled["enabled"] = True

    def fail_operationally(
        _context: dict,
        *_args: object,
        evaluation_outcome: dict[str, str],
        **_kwargs: object,
    ) -> None:
        evaluation_outcome.update({"status": "operational_failure", "reason": "proposer_invalid"})
        raise bot.ApiError("AI-first reply pipeline operational failure", service="xai")

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ai_first_reply_strategy", enabled)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MY_USERNAME", "MrsMThatcher")
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: [dict(mention)])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    monkeypatch.setattr(
        bot,
        "build_context_for_reply_ai",
        lambda *_args: (unit_reply_context(contribution=mention["text"]), True),
    )
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(bot, "generate_ai_first_reply", fail_operationally)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_API_ERROR
    assert state.get("reply_evaluation_records", {}) == {}
    assert state.get("last_seen_mention_id") is None


def test_runtime_config_cannot_raise_reply_length_above_x_limit() -> None:
    errors = bot.validate_runtime_config_values({"MAX_REPLY_CHARS": 281})

    assert "MAX_REPLY_CHARS must not exceed 280" in errors


@pytest.mark.parametrize("value", [True, 270.0, "270"])
def test_runtime_config_requires_integer_reply_length(value: object) -> None:
    errors = bot.validate_runtime_config_values({"MAX_REPLY_CHARS": value})

    assert "MAX_REPLY_CHARS must be an integer" in errors


def test_original_image_selection_returns_observability_and_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    original = image_dir / "t44.jpg"
    original.write_bytes(b"original")
    analysis_path = tmp_path / "image_analysis.json"
    analysis = image_analysis_for_paths([original])
    write_image_analysis(analysis_path, analysis)

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", False)
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", analysis_path)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    caplog.set_level(logging.INFO, logger=bot.log.name)

    chosen = bot.choose_matched_unused_image(
        set(),
        {"quote_hash": "f" * 64, "analysis": {"primary_topics": [], "secondary_topics": [], "tone": [], "visual_energy": "low"}},
        {},
    )

    assert chosen["basename"] == "t44.jpg"
    assert chosen["image_source"] == "original"
    assert chosen["origin_quote_hash"] is None
    assert chosen["origin_quote_match"] is False
    assert chosen["origin_quote_boost"] == 0.0
    assert "REGULAR_IMAGE_SELECTED source=original basename=t44.jpg" in caplog.text
    assert "origin_quote_match=false origin_quote_boost=0.0" in caplog.text


def test_generated_image_origin_quote_boost_can_select_matching_generated_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    quote_hash = "c" * 64
    generated = generated_dir / f"tg_{quote_hash}.png"
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    neutral = {
        "pairing": {},
        "themes": [],
        "tone": [],
        "visual_energy": "low",
        "quality": {},
        "seasonality": {"avoid_outside_season_or_occasion": False},
    }
    write_image_analysis(original_analysis_path, image_analysis_for_paths([original], {"t01.jpg": neutral}))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths([generated], {generated.name: neutral}))

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST", 10)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))

    chosen = bot.choose_matched_unused_image(
        set(),
        {
            "quote_hash": quote_hash,
            "analysis": {"primary_topics": [], "secondary_topics": [], "tone": [], "visual_energy": "low"},
        },
        {},
    )

    assert chosen["basename"] == generated.name
    assert chosen["components"]["generated_origin_quote"] == 10.0
    assert chosen["image_source"] == "generated"
    assert chosen["origin_quote_hash"] == quote_hash
    assert chosen["origin_quote_match"] is True
    assert chosen["origin_quote_boost"] == 10.0


def test_generated_image_cross_quote_selection_gets_no_origin_boost_and_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated_dir = tmp_path / "generated"
    image_dir = tmp_path / "images"
    generated_dir.mkdir()
    image_dir.mkdir()
    origin_quote_hash = "a" * 64
    selected_quote_hash = "b" * 64
    generated = generated_dir / f"tg_{origin_quote_hash}.png"
    generated.write_bytes(b"generated")
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    write_image_analysis(original_analysis_path, image_analysis_for_paths([]))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths([generated]))

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST", 10)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    caplog.set_level(logging.INFO, logger=bot.log.name)

    chosen = bot.choose_matched_unused_image(
        set(),
        {
            "quote_hash": selected_quote_hash,
            "analysis": {"primary_topics": [], "secondary_topics": [], "tone": [], "visual_energy": "low"},
        },
        {},
    )

    assert chosen["basename"] == generated.name
    assert chosen["image_source"] == "generated"
    assert chosen["origin_quote_hash"] == origin_quote_hash
    assert chosen["origin_quote_match"] is False
    assert chosen["origin_quote_boost"] == 0.0
    assert "generated_origin_quote" not in chosen["components"]
    assert f"REGULAR_IMAGE_SELECTED source=generated basename={generated.name}" in caplog.text
    assert f"origin_quote_hash={origin_quote_hash} origin_quote_match=false origin_quote_boost=0.0" in caplog.text


def test_generated_image_blocked_by_spacing_selects_original_without_marking_generated_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    generated_hash = "a" * 64
    generated = generated_dir / f"tg_{generated_hash}.png"
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Tax quote.\n", encoding="utf-8")
    quote_analysis = {"primary_topics": ["tax"], "secondary_topics": [], "tone": [], "visual_energy": "low"}
    original_analysis = {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}}
    generated_analysis = {"pairing": {"best_for_topics": ["tax"]}, "themes": ["tax"], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}}
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    write_image_analysis(original_analysis_path, image_analysis_for_paths([original], {original.name: original_analysis}))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths([generated], {generated.name: generated_analysis}))

    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(["Tax quote."], {0: quote_analysis}))
    caplog.set_level(logging.INFO, logger=bot.log.name)
    images_used: set[str] = set()
    state = {"original_regular_posts_since_generated_image": 0}

    chosen = bot.choose_regular_quote_image_pair(
        set(),
        images_used,
        state,
    )[1]

    assert chosen["basename"] == "t01.jpg"
    assert chosen["image_source"] == "original"
    assert generated.name not in images_used
    assert "GENERATED_IMAGE_SPACING_STATUS pool_enabled=true allowed=false original_posts_since_generated=0 required=2" in caplog.text
    assert "GENERATED_IMAGE_POOL_BLOCKED_BY_SPACING original_posts_since_generated=0 required=2" in caplog.text


def test_generated_image_spacing_expiry_allows_generated_to_compete_with_origin_boost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    quote = "Origin boost quote."
    quote_hash = bot.quote_text_hash(quote)
    generated = generated_dir / f"tg_{quote_hash}.png"
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text(quote + "\n", encoding="utf-8")
    neutral = {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}, "seasonality": {"avoid_outside_season_or_occasion": False}}
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    write_image_analysis(original_analysis_path, image_analysis_for_paths([original], {original.name: neutral}))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths([generated], {generated.name: neutral}))

    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST", 4)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines([quote]))

    chosen = bot.choose_regular_quote_image_pair(
        set(),
        set(),
        {"original_regular_posts_since_generated_image": 2},
    )[1]

    assert chosen["basename"] == generated.name
    assert chosen["image_source"] == "generated"
    assert chosen["origin_quote_match"] is True
    assert chosen["origin_quote_boost"] == 4.0


def test_generated_origin_boost_preserves_expected_final_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated_dir = tmp_path / "generated"
    image_dir = tmp_path / "images"
    generated_dir.mkdir()
    image_dir.mkdir()
    quote_hash = "c" * 64
    generated = generated_dir / f"tg_{quote_hash}.png"
    generated.write_bytes(b"generated")
    analysis_obj = {
        "pairing": {"best_for_topics": ["trade"]},
        "themes": [],
        "tone": [],
        "visual_energy": "low",
        "quality": {},
        "seasonality": {"avoid_outside_season_or_occasion": False},
    }
    quote_analysis = {"primary_topics": ["trade"], "secondary_topics": [], "tone": [], "visual_energy": "low"}
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    write_image_analysis(original_analysis_path, image_analysis_for_paths([]))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths([generated], {generated.name: analysis_obj}))

    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST", 6)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))

    base_score = bot.score_image_for_quote(quote_analysis, analysis_obj, bot.build_image_topic_idf(bot.load_image_analysis()))[0]
    origin_choice = bot.choose_matched_unused_image(set(), {"quote_hash": quote_hash, "analysis": quote_analysis}, {})
    cross_choice = bot.choose_matched_unused_image(set(), {"quote_hash": "d" * 64, "analysis": quote_analysis}, {})

    assert origin_choice["score"] == pytest.approx(base_score + 6)
    assert origin_choice["origin_quote_boost"] == 6.0
    assert cross_choice["score"] == pytest.approx(base_score)
    assert cross_choice["origin_quote_boost"] == 0.0


def test_generated_image_pool_enabled_does_not_migrate_legacy_image_indices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original = image_dir / "t01.jpg"
    generated = generated_dir / ("tg_" + "d" * 64 + ".png")
    original.write_bytes(b"original")
    generated.write_bytes(b"generated")
    analysis = bot.merge_image_analysis(
        image_analysis_for_paths([original]),
        image_analysis_for_paths([generated]),
    )
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)

    normalised, changed = bot.normalise_image_used_basenames({0}, [str(original), str(generated)], analysis)

    assert normalised == {0}
    assert not changed


def test_highest_scoring_unused_image_is_selected_even_if_global_best_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_paths = []
    for name in ["t01.jpg", "t02.jpg"]:
        path = image_dir / name
        path.write_bytes(name.encode("utf-8"))
        image_paths.append(path)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(
        bot,
        "load_image_analysis",
        lambda: image_analysis_for_paths(
            image_paths,
            {
                "t01.jpg": {"pairing": {"best_for_topics": ["rare_topic"]}, "themes": ["rare_topic"], "visual_energy": "low", "quality": {}},
                "t02.jpg": {"pairing": {"best_for_topics": ["government"]}, "themes": [], "visual_energy": "low", "quality": {}},
            },
        ),
    )

    chosen = bot.choose_matched_unused_image(
        {"t01.jpg"},
        {"analysis": {"primary_topics": ["rare_topic", "government"], "secondary_topics": [], "tone": [], "visual_energy": "low"}},
        {},
    )

    assert chosen["basename"] == "t02.jpg"


def test_idf_makes_rare_topic_match_more_valuable_than_ubiquitous_topic() -> None:
    image_analysis = {
        "items": {
            "a": {"analysis": {"pairing": {"best_for_topics": ["leadership", "rare_topic"]}, "themes": []}},
            "b": {"analysis": {"pairing": {"best_for_topics": ["leadership"]}, "themes": []}},
            "c": {"analysis": {"pairing": {"best_for_topics": ["leadership"]}, "themes": []}},
        }
    }
    idf = bot.build_image_topic_idf(image_analysis)
    quote = {"primary_topics": ["rare_topic"], "secondary_topics": [], "tone": [], "visual_energy": "low"}
    rare_image = {"pairing": {"best_for_topics": ["rare_topic"]}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}}
    common_image = {"pairing": {"best_for_topics": ["leadership"]}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}}

    rare_score = bot.score_image_for_quote(quote, rare_image, idf)[0]
    common_score = bot.score_image_for_quote({"primary_topics": ["leadership"], "secondary_topics": [], "tone": [], "visual_energy": "low"}, common_image, idf)[0]

    assert rare_score > common_score


def test_visual_energy_exact_match_beats_opposite_mismatch() -> None:
    quote = {"primary_topics": [], "secondary_topics": [], "tone": [], "visual_energy": "high"}
    exact = {"pairing": {}, "themes": [], "tone": [], "visual_energy": "high", "quality": {}}
    opposite = {"pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}}

    assert bot.score_image_for_quote(quote, exact)[0] > bot.score_image_for_quote(quote, opposite)[0]


def test_strong_visual_contradiction_is_ineligible() -> None:
    quote = {
        "primary_topics": [],
        "secondary_topics": [],
        "tone": [],
        "visual_energy": "low",
        "archive_image_preferences": {"strong_visual_mismatches": ["crowd scenes"]},
    }
    image = {"description": "A large crowd scene outside a building.", "pairing": {}, "themes": [], "tone": [], "visual_energy": "low", "quality": {}}

    score, components, eligible = bot.score_image_for_quote(quote, image)

    assert eligible is False
    assert score <= bot.IMAGE_STRONG_MISMATCH_PENALTY
    assert "strong_mismatch" in components


def test_hard_mismatch_requires_both_tokens_for_two_token_phrase() -> None:
    assert not bot.hard_mismatch_phrase_matches_text("armed conflict", "An armed police officer stands nearby.")
    assert bot.hard_mismatch_phrase_matches_text("armed conflict", "An armed conflict scene is shown.")


def test_hard_mismatch_longer_phrase_uses_ceil_coverage() -> None:
    assert not bot.hard_mismatch_phrase_matches_text("large military parade crowd", "Large military display")
    assert bot.hard_mismatch_phrase_matches_text("large military parade crowd", "Large military parade crowd")


def test_phrase_match_requires_both_tokens_for_two_token_phrase() -> None:
    assert not bot.phrase_matches_text(
        "informal portrait",
        "A formal portrait against a conference backdrop.",
    )
    assert not bot.phrase_matches_text(
        "parliamentary setting",
        "A domestic setting with patterned wallpaper.",
    )
    assert bot.phrase_matches_text(
        "informal portrait",
        "A warm informal portrait in a private room.",
    )
    assert bot.phrase_matches_text(
        "parliamentary setting",
        "The photograph shows a parliamentary setting.",
    )


def test_phrase_match_preserves_longer_phrase_partial_coverage() -> None:
    phrase = "formal political conference address"

    assert bot.phrase_matches_text(
        phrase,
        "A formal political address by the party leader.",
    )
    assert not bot.phrase_matches_text(
        phrase,
        "A formal address in a domestic room.",
    )


def test_post_random_quote_retries_alternate_quote_when_first_has_no_image_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Bad visual quote.\nGood visual quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "LINES_USED_FILE", tmp_path / "lines_used.json")
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", tmp_path / "images_used.json")
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot.random, "uniform", lambda low, high: low)
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "950001"}}
        ),
    )
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "maybe_schedule_meme_after_quote_post", lambda state, quote_post_epoch=None, **kwargs: None)
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda name, **fields: events.append((name, fields)))
    monkeypatch.setattr(
        bot,
        "load_quote_analysis",
        lambda: quote_analysis_for_lines(
            ["Bad visual quote.", "Good visual quote."],
            {
                0: {
                        "archive_image_preferences": {"strong_visual_mismatches": ["formal portrait"]},
                        "seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"},
                },
                1: {
                        "archive_image_preferences": {},
                        "seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"},
                },
            },
        ),
    )
    monkeypatch.setattr(
        bot,
        "load_image_analysis",
        lambda: image_analysis_for_paths(
            [image_path],
            {
                "t01.jpg": {
                        "description": "Formal portrait in a studio",
                        "pairing": {},
                        "themes": [],
                        "tone": [],
                        "visual_energy": "low",
                        "quality": {},
                        "seasonality": {"avoid_outside_season_or_occasion": False},
                }
            },
        ),
    )

    lines_used: set[int] = set()
    images_used: set[str] = set()
    state: dict = {}
    bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == {bot.quote_text_hash("Good visual quote.")}
    assert images_used == {"t01.jpg"}
    assert state["last_regular_image_filename"] == "t01.jpg"
    posted = next(fields for name, fields in events if name == "main_post_posted")
    assert posted["quote_hash"] == bot.quote_text_hash("Good visual quote.")
    assert posted["image_hash"] == hashlib.sha256(b"fake").hexdigest()


def configure_generated_cycle_recovery_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    original_analyses: dict[str, dict],
    generated_analyses: dict[str, dict],
    quote_analyses: dict[int, dict],
    quotes: list[str] | None = None,
) -> tuple[set[str], set[str], dict, dict[str, Path], dict[str, Path]]:
    quotes = quotes or ["Quote A.", "Quote B."]
    image_dir = tmp_path / "images"
    generated_dir = tmp_path / "generated"
    image_dir.mkdir()
    generated_dir.mkdir()
    original_paths: dict[str, Path] = {}
    generated_paths: dict[str, Path] = {}
    for basename in original_analyses:
        path = image_dir / basename
        path.write_bytes(f"original-{basename}".encode("utf-8"))
        original_paths[basename] = path
    for basename in generated_analyses:
        path = generated_dir / basename
        path.write_bytes(f"generated-{basename}".encode("utf-8"))
        generated_paths[basename] = path

    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(quotes) + "\n", encoding="utf-8")
    original_analysis_path = tmp_path / "image_analysis.json"
    generated_analysis_path = tmp_path / "generated_image_analysis.json"
    write_image_analysis(original_analysis_path, image_analysis_for_paths(list(original_paths.values()), original_analyses))
    write_image_analysis(generated_analysis_path, image_analysis_for_paths(list(generated_paths.values()), generated_analyses))

    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated_dir))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", original_analysis_path)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_ANALYSIS_FILE", str(generated_analysis_path))
    monkeypatch.setattr(bot, "LINES_USED_FILE", tmp_path / "lines_used.json")
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", tmp_path / "images_used.json")
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", tmp_path / "regular_post_receipt.json")
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot.random, "uniform", lambda low, high: low)
    monkeypatch.setattr(bot.random, "randint", lambda low, high: low)
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "950001"}}
        ),
    )
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(quotes, quote_analyses))
    return set(), set(), {}, original_paths, generated_paths


def capture_create_post_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake_create_post(**kwargs: object) -> dict:
        calls.append(dict(kwargs))
        return mock_confirmed_main_post(
            kwargs,
            {"data": {"id": "950001"}},
        )

    monkeypatch.setattr(bot, "create_post", fake_create_post)
    return calls


def portrait_analysis() -> dict:
    return {
        "description": "Formal portrait in a studio",
        "pairing": {},
        "themes": [],
        "tone": [],
        "visual_energy": "low",
        "quality": {},
        "seasonality": {"avoid_outside_season_or_occasion": False},
    }


def crowd_scene_analysis() -> dict:
    return {
        "description": "A large crowd scene outside a building",
        "pairing": {},
        "themes": [],
        "tone": [],
        "visual_energy": "low",
        "quality": {},
        "seasonality": {"avoid_outside_season_or_occasion": False},
    }


def quote_rejecting_crowd_scenes() -> dict:
    return {
        "archive_image_preferences": {"strong_visual_mismatches": ["crowd scene"]},
        "seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"},
        "primary_topics": [],
        "secondary_topics": [],
        "tone": [],
        "visual_energy": "low",
    }


def test_post_random_quote_recovers_when_remaining_generated_cycle_image_cannot_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated_hash = "a" * 64
    generated_name = f"tg_{generated_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": portrait_analysis()},
        generated_analyses={generated_name: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes(), 1: quote_rejecting_crowd_scenes()},
    )
    images_used.add("t01.jpg")
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert images_used == {"t01.jpg"}
    assert state["last_regular_image_filename"] == "t01.jpg"
    assert lines_used == {bot.quote_text_hash("Quote A.")}
    assert "resetting image cycle and retrying once" in caplog.text
    assert "Regular quote/image pairing succeeded after image-cycle recovery" in caplog.text
    assert "last image permitted" not in caplog.text


def test_image_cycle_recovery_does_not_reenable_spacing_blocked_generated_images(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated_hash = "9" * 64
    generated_name = f"tg_{generated_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": crowd_scene_analysis()},
        generated_analyses={generated_name: portrait_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.add("t01.jpg")
    state["original_regular_posts_since_generated_image"] = 0
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
    create_calls = capture_create_post_calls(monkeypatch)
    caplog.set_level(logging.INFO, logger=bot.log.name)

    with pytest.raises(RuntimeError, match="No eligible regular quote/image pair found"):
        bot.post_random_quote(lines_used, images_used, state)

    assert state.get("last_regular_image_filename") is None
    assert images_used == {"t01.jpg"}
    assert generated_name not in images_used
    assert create_calls == []
    assert state["original_regular_posts_since_generated_image"] == 0
    assert "resetting image cycle and retrying once" in caplog.text
    assert "GENERATED_IMAGE_POOL_BLOCKED_BY_SPACING" in caplog.text
    assert f"REGULAR_IMAGE_SELECTED source=generated basename={generated_name}" not in caplog.text


def test_last_image_boundary_fallback_recovers_two_image_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated_hash = "f" * 64
    generated_name = f"tg_{generated_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": portrait_analysis()},
        generated_analyses={generated_name: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.add("t01.jpg")
    state["last_regular_image_filename"] = "t01.jpg"
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_regular_image_filename"] == "t01.jpg"
    assert images_used == {"t01.jpg"}
    assert lines_used == {bot.quote_text_hash("Only quote.")}
    assert "retrying once with last image permitted" in caplog.text
    assert "Regular quote/image pairing succeeded after permitting last regular image" in caplog.text


def test_last_image_boundary_avoidance_wins_when_non_last_image_is_viable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated_hash = "1" * 64
    generated_name = f"tg_{generated_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": portrait_analysis(), "t02.jpg": portrait_analysis()},
        generated_analyses={generated_name: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.update({"t01.jpg", "t02.jpg"})
    state["last_regular_image_filename"] = "t01.jpg"
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_regular_image_filename"] == "t02.jpg"
    assert images_used == {"t02.jpg"}
    assert "Temporarily excluded last regular image at forced eligible-cycle boundary: t01.jpg" in caplog.text
    assert "retrying once with last image permitted" not in caplog.text


def test_last_image_boundary_fallback_fails_safely_without_fourth_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated_hash = "2" * 64
    generated_name = f"tg_{generated_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": crowd_scene_analysis()},
        generated_analyses={generated_name: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.add("t01.jpg")
    state["last_regular_image_filename"] = "t01.jpg"
    original_lines = set(lines_used)
    original_images = set(images_used)
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: pytest.fail("upload_media should not be called"))
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called"))
    caplog.set_level(logging.INFO, logger=bot.log.name)

    with pytest.raises(RuntimeError, match="No eligible regular quote/image pair found"):
        bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == original_lines
    assert images_used == original_images
    assert caplog.text.count("Forcing eligible image cycle reset for regular quote/image pairing recovery") == 2
    assert caplog.text.count("retrying once with last image permitted") == 1
    assert "No viable regular quote/image pair found after final last-image recovery fallback" in caplog.text


def test_no_last_image_does_not_trigger_last_image_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated_hash = "3" * 64
    generated_name = f"tg_{generated_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": portrait_analysis()},
        generated_analyses={generated_name: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.add("t01.jpg")
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_regular_image_filename"] == "t01.jpg"
    assert "retrying once with last image permitted" not in caplog.text


def test_last_image_boundary_fallback_can_reuse_generated_previous_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    good_hash = "4" * 64
    bad_hash = "5" * 64
    good_generated = f"tg_{good_hash}.png"
    bad_generated = f"tg_{bad_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={},
        generated_analyses={
            good_generated: portrait_analysis(),
            bad_generated: crowd_scene_analysis(),
        },
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.add(good_generated)
    state["last_regular_image_filename"] = good_generated
    create_calls = capture_create_post_calls(monkeypatch)
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_regular_image_filename"] == good_generated
    assert images_used == {good_generated}
    assert len(create_calls) == 1
    assert create_calls[0]["made_with_ai"] is True
    assert "retrying once with last image permitted" in caplog.text
    assert "REGULAR_IMAGE_SELECTED source=generated" in caplog.text


def test_last_image_fallback_does_not_reenable_spacing_blocked_generated_previous_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    last_hash = "8" * 64
    last_generated = f"tg_{last_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": crowd_scene_analysis()},
        generated_analyses={last_generated: portrait_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.add(last_generated)
    state["last_regular_image_filename"] = last_generated
    state["original_regular_posts_since_generated_image"] = 0
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: pytest.fail("upload_media should not be called"))
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called"))
    caplog.set_level(logging.INFO, logger=bot.log.name)

    with pytest.raises(RuntimeError, match="No eligible regular quote/image pair found"):
        bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == set()
    assert images_used == {last_generated}
    assert "retrying once with last image permitted" not in caplog.text
    assert f"REGULAR_IMAGE_SELECTED source=generated basename={last_generated}" not in caplog.text


def test_post_random_quote_image_cycle_recovery_fails_safely_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated_hash = "b" * 64
    generated_name = f"tg_{generated_hash}.png"
    lines_used, images_used, _state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": crowd_scene_analysis()},
        generated_analyses={generated_name: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes(), 1: quote_rejecting_crowd_scenes()},
    )
    images_used.add("t01.jpg")
    original_lines = set(lines_used)
    original_images = set(images_used)
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: pytest.fail("upload_media should not be called"))
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called"))
    caplog.set_level(logging.INFO, logger=bot.log.name)

    with pytest.raises(RuntimeError, match="No eligible regular quote/image pair found"):
        bot.post_random_quote(lines_used, images_used, {})

    assert lines_used == original_lines
    assert images_used == original_images
    assert caplog.text.count("resetting image cycle and retrying once") == 1
    assert "No viable regular quote/image pair found after image-cycle recovery" in caplog.text


def test_forced_image_cycle_recovery_respects_generated_last_image_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    last_hash = "c" * 64
    bad_hash = "d" * 64
    last_generated = f"tg_{last_hash}.png"
    bad_generated = f"tg_{bad_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": portrait_analysis()},
        generated_analyses={
            last_generated: portrait_analysis(),
            bad_generated: crowd_scene_analysis(),
        },
        quote_analyses={0: quote_rejecting_crowd_scenes(), 1: quote_rejecting_crowd_scenes()},
    )
    images_used.update({"t01.jpg", last_generated})
    state["last_regular_image_filename"] = last_generated

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_regular_image_filename"] == "t01.jpg"
    assert last_generated not in images_used
    assert "t01.jpg" in images_used


def test_successful_current_cycle_pair_does_not_force_image_cycle_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generated_hash = "e" * 64
    generated_name = f"tg_{generated_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": portrait_analysis()},
        generated_analyses={generated_name: portrait_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes(), 1: quote_rejecting_crowd_scenes()},
    )
    images_used.add("t01.jpg")
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_regular_image_filename"] == generated_name
    assert "resetting image cycle and retrying once" not in caplog.text


def test_global_image_failure_does_not_trigger_image_cycle_recovery(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(bot, "reconcile_main_post_receipts", lambda *args, **kwargs: {"regular": False, "meme": False})
    monkeypatch.setattr(bot, "quote_used_history_has_legacy_indices", lambda used: False)
    monkeypatch.setattr(
        bot,
        "choose_unused_line_candidate",
        lambda *args, **kwargs: {
            "line_no": 0,
            "quote_hash": bot.quote_text_hash("Good quote."),
            "text": "Good quote.",
            "analysis": {},
        },
    )
    monkeypatch.setattr(
        bot,
        "choose_matched_unused_image",
        lambda *args, **kwargs: (_ for _ in ()).throw(bot.GlobalImageUnavailable("no images")),
    )
    caplog.set_level(logging.INFO, logger=bot.log.name)

    with pytest.raises(bot.GlobalImageUnavailable):
        bot.post_random_quote(set(), set(), {})

    assert "resetting image cycle and retrying once" not in caplog.text


def test_original_regular_image_posts_without_made_with_ai(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    state["original_regular_posts_since_generated_image"] = 0
    create_calls = capture_create_post_calls(monkeypatch)
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert len(create_calls) == 1
    assert create_calls[0]["made_with_ai"] is False
    assert state["original_regular_posts_since_generated_image"] == 1
    assert "GENERATED_IMAGE_SPACING_STATE_UPDATED pool_enabled=false allowed=false original_posts_since_generated=1 required=2 image_source=original image=t01.jpg" in caplog.text


def test_second_original_regular_image_spacing_update_allows_generated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    state["original_regular_posts_since_generated_image"] = 1
    capture_create_post_calls(monkeypatch)
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["original_regular_posts_since_generated_image"] == 2
    assert "GENERATED_IMAGE_SPACING_STATE_UPDATED pool_enabled=false allowed=true original_posts_since_generated=2 required=2 image_source=original image=t01.jpg" in caplog.text


def test_generated_origin_match_regular_image_posts_with_made_with_ai(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    quote = "Generated origin quote."
    origin_hash = bot.quote_text_hash(quote)
    generated_name = f"tg_{origin_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={},
        generated_analyses={generated_name: portrait_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=[quote],
    )
    create_calls = capture_create_post_calls(monkeypatch)

    bot.post_random_quote(lines_used, images_used, state)

    assert len(create_calls) == 1
    assert create_calls[0]["made_with_ai"] is True
    assert state["last_regular_image_filename"] == generated_name
    assert state["original_regular_posts_since_generated_image"] == 0


def test_generated_cross_quote_regular_image_posts_with_made_with_ai(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    quote = "Generated cross quote."
    origin_hash = "6" * 64
    assert origin_hash != bot.quote_text_hash(quote)
    generated_name = f"tg_{origin_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={},
        generated_analyses={generated_name: portrait_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=[quote],
    )
    create_calls = capture_create_post_calls(monkeypatch)
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert len(create_calls) == 1
    assert create_calls[0]["made_with_ai"] is True
    assert state["last_regular_image_filename"] == generated_name
    assert state["original_regular_posts_since_generated_image"] == 0
    assert f"GENERATED_IMAGE_SPACING_STATE_UPDATED pool_enabled=true allowed=false original_posts_since_generated=0 required=2 image_source=generated image={generated_name}" in caplog.text
    assert f"REGULAR_IMAGE_SELECTED source=generated basename={generated_name}" in caplog.text
    assert f"origin_quote_hash={origin_hash} origin_quote_match=false origin_quote_boost=0.0" in caplog.text


def test_generated_image_selected_after_cycle_recovery_posts_with_made_with_ai(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin_hash = "7" * 64
    generated_name = f"tg_{origin_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={"t01.jpg": crowd_scene_analysis()},
        generated_analyses={generated_name: portrait_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.add(generated_name)
    create_calls = capture_create_post_calls(monkeypatch)

    bot.post_random_quote(lines_used, images_used, state)

    assert len(create_calls) == 1
    assert create_calls[0]["made_with_ai"] is True
    assert state["last_regular_image_filename"] == generated_name
    assert state["original_regular_posts_since_generated_image"] == 0


def test_failed_regular_post_attempt_does_not_change_generated_spacing_counter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    quote = "Generated failure quote."
    origin_hash = bot.quote_text_hash(quote)
    generated_name = f"tg_{origin_hash}.png"
    lines_used, images_used, state, _original_paths, _generated_paths = configure_generated_cycle_recovery_post(
        tmp_path,
        monkeypatch,
        original_analyses={},
        generated_analyses={generated_name: portrait_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=[quote],
    )
    state["original_regular_posts_since_generated_image"] = 2
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs,
            {"data": {"id": "banana"}},
        ),
    )
    caplog.set_level(logging.INFO, logger=bot.log.name)

    with pytest.raises(RuntimeError, match="did not return a valid post id"):
        bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == set()
    assert images_used == set()
    assert state["original_regular_posts_since_generated_image"] == 2
    assert "GENERATED_IMAGE_SPACING_STATE_UPDATED" not in caplog.text


def test_post_random_quote_restores_histories_when_all_pair_attempts_fail_after_cycle_resets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    t01 = image_dir / "t01.jpg"
    t23 = image_dir / "t23.jpg"
    t01.write_bytes(b"fake-1")
    t23.write_bytes(b"fake-23")
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Christmas quote.\nBad quote A.\nBad quote B.\n", encoding="utf-8")
    lines_used_file = tmp_path / "lines_used.json"
    images_used_file = tmp_path / "images_used.json"
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "LINES_USED_FILE", lines_used_file)
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", images_used_file)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot.random, "uniform", lambda low, high: low)
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: pytest.fail("upload_media should not be called"))
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called"))
    monkeypatch.setattr(
        bot,
        "load_quote_analysis",
        lambda: quote_analysis_for_lines(
            ["Christmas quote.", "Bad quote A.", "Bad quote B."],
            {
                0: {
                        "archive_image_preferences": {},
                        "seasonality": {
                            "hard_exclude_outside_windows": True,
                            "preferred_windows": [{"start": "12-10", "end": "12-28"}],
                            "relevance": "christmas",
                        },
                },
                1: {
                        "archive_image_preferences": {"strong_visual_mismatches": ["formal portrait"]},
                        "seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"},
                },
                2: {
                        "archive_image_preferences": {"strong_visual_mismatches": ["formal portrait"]},
                        "seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"},
                },
            },
        ),
    )
    monkeypatch.setattr(
        bot,
        "load_image_analysis",
        lambda: image_analysis_for_paths(
            [t01, t23],
            {
                "t01.jpg": {
                        "description": "Formal portrait in a studio",
                        "pairing": {},
                        "themes": [],
                        "tone": [],
                        "visual_energy": "low",
                        "quality": {},
                        "seasonality": {"avoid_outside_season_or_occasion": False},
                },
                "t23.jpg": {
                        "description": "Christmas scene",
                        "pairing": {},
                        "themes": [],
                        "tone": [],
                        "visual_energy": "low",
                        "quality": {},
                        "seasonality": {"avoid_outside_season_or_occasion": True, "occasions": ["christmas"]},
                },
            },
        ),
    )

    lines_used: set[str] = {bot.quote_text_hash("Bad quote A."), bot.quote_text_hash("Bad quote B.")}
    images_used: set[str] = {"t01.jpg"}
    original_lines = set(lines_used)
    original_images = set(images_used)

    with pytest.raises(RuntimeError, match="used histories unchanged"):
        bot.post_random_quote(lines_used, images_used, {})

    assert lines_used == original_lines
    assert images_used == original_images
    assert not lines_used_file.exists()
    assert not images_used_file.exists()


def test_post_random_quote_requires_created_post_id_before_marking_histories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    lines_used_file = tmp_path / "lines_used.json"
    images_used_file = tmp_path / "images_used.json"
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "LINES_USED_FILE", lines_used_file)
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", images_used_file)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(kwargs, {"data": {}}),
    )
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: pytest.fail("save_state should not be called"))
    monkeypatch.setattr(bot, "maybe_schedule_meme_after_quote_post", lambda *args, **kwargs: pytest.fail("meme scheduling should not be called"))
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: pytest.fail("cache_tweet should not be called"))
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: pytest.fail("record_recent_own_post should not be called"))
    monkeypatch.setattr(bot, "log_event", lambda event, **kwargs: events.append((event, kwargs)))
    monkeypatch.setattr(
        bot,
        "load_quote_analysis",
        lambda: quote_analysis_for_lines(
            ["Good quote."],
            {
                0: {
                        "archive_image_preferences": {},
                        "seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"},
                }
            },
        ),
    )
    monkeypatch.setattr(
        bot,
        "load_image_analysis",
        lambda: image_analysis_for_paths(
            [image_path],
            {
                "t01.jpg": {
                        "description": "A usable image",
                        "pairing": {},
                        "themes": [],
                        "tone": [],
                        "visual_energy": "low",
                        "quality": {},
                        "seasonality": {"avoid_outside_season_or_occasion": False},
                }
            },
        ),
    )

    lines_used: set[int] = set()
    images_used: set[str] = set()
    state = {"last_regular_image_filename": "previous.jpg"}

    with pytest.raises(RuntimeError, match="valid post id"):
        bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == set()
    assert images_used == set()
    assert state["last_regular_image_filename"] == "previous.jpg"
    assert not lines_used_file.exists()
    assert not images_used_file.exists()
    assert events == []


def configure_simple_quote_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    create_response: dict | None = None,
) -> tuple[set[str], set[str], dict, Path, Path, Path, Path]:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    lines_used_file = tmp_path / "lines_used.json"
    images_used_file = tmp_path / "images_used.json"
    receipt_file = tmp_path / "regular_post_receipt.json"
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "LINES_USED_FILE", lines_used_file)
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", images_used_file)
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs,
            create_response or {"data": {"id": "950001"}},
        ),
    )
    monkeypatch.setattr(bot, "maybe_schedule_meme_after_quote_post", lambda state, quote_post_epoch=None, **kwargs: None)
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(["Good quote."]))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: image_analysis_for_paths([image_path]))
    return set(), set(), {}, lines_used_file, images_used_file, receipt_file, lines_file


def mock_confirmed_main_post(
    kwargs: dict[str, object],
    response: dict,
) -> dict:
    """Make a main-post double cross the real durable transport lifecycle."""
    attempt = kwargs.get("prepared_main_post_attempt")
    authority = kwargs.get("prepared_transport_authority")
    source = kwargs.get("prepared_transport_source")
    if (
        isinstance(attempt, dict)
        and isinstance(authority, bot.TransportAuthority)
        and isinstance(source, bot.SourceReceiptBinding)
    ):
        armed = bot.arm_transport_transaction(
            Path(authority.journal_path),
            authority,
            mutation_authority=bot.transaction_mutation_authority(
                "focused transport arming"
            ),
        )
        bot.consume_transport_authority(
            Path(armed.journal_path),
            armed,
            method="POST",
            request_path="/2/tweets",
            payload=source.request.payload(),
            expected_receipt_path=Path(source.receipt_path),
        )
        post_id = response.get("data", {}).get("id")
        if bot.valid_post_id(post_id):
            confirmation_epoch = bot.confirmation_epoch_after_remote_success(
                attempt
            )
            bot.confirm_transport_transaction(
                Path(armed.journal_path),
                armed,
                mutation_authority=bot.transaction_mutation_authority(
                    "focused transport confirmation"
                ),
                post_id=str(post_id),
                confirmation_epoch=confirmation_epoch,
            )
    return json.loads(json.dumps(response))


def install_receipt_bound_x_request_stub(
    monkeypatch: pytest.MonkeyPatch,
    callback: object,
) -> None:
    """Make a low-level X stub preserve the real final authority check.

    Tests which replace ``x_request`` still need to consume the exact durable
    transport authority at the point represented by the fake transport.  A
    plain response lambda would otherwise bypass the production boundary and
    make later confirmation fail for the wrong reason.
    """

    def bound_request(method: str, path: str, **kwargs: object) -> object:
        if bot.x_request_targets_tweet_create(method, path):
            authority = kwargs.get("_remote_write_authorization")
            assert isinstance(authority, bot.TransportAuthority)
            expected_receipt_path = bot.canonical_transport_receipt_path_for_lane(
                authority.lane
            )
            assert expected_receipt_path is not None
            bot.block_if_unrelated_receipt_appeared_for_tweet_transport(
                expected_receipt_path
            )
            payload = kwargs.get("json")
            assert isinstance(payload, dict)
            bot.consume_transport_authority(
                Path(authority.journal_path),
                authority,
                method=method,
                request_path="/2/tweets",
                payload=payload,
                expected_receipt_path=expected_receipt_path,
            )
        assert callable(callback)
        return callback(method, path, **kwargs)

    monkeypatch.setattr(bot, "x_request", bound_request)


def configure_simple_meme_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict, Path]:
    """Configure one entirely local daily-meme transaction."""
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    receipt_file = tmp_path / "meme_post_receipt.json"
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda _path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)
    return {
        "next_meme_post_epoch": 1_799_999_000,
        "posted_meme_filenames": [],
    }, receipt_file


def valid_regular_receipt(**overrides: object) -> dict:
    receipt = {
        "schema_version": 1,
        "post_id": "950001",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    receipt.update(overrides)
    return receipt


def valid_regular_receipt_v2(**overrides: object) -> dict:
    receipt = valid_regular_receipt(schema_version=2)
    receipt["quote_history_after"] = [str(receipt["quote_hash"])]
    receipt["image_history_after"] = [str(receipt["image_basename"])]
    receipt.update(overrides)
    return receipt


def test_durable_receipt_creation_rejects_replaced_coercion_equal_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "receipt.json"

    def replace_during_parent_sync(
        target: Path,
        *,
        strict: bool = False,
    ) -> None:
        assert strict is True
        target.unlink()
        target.write_bytes(bot.canonical_atomic_json_bytes({"field": True}))
        target.chmod(0o600)

    monkeypatch.setattr(bot, "fsync_parent_dir", replace_during_parent_sync)

    with pytest.raises(
        bot.UnsafeReceiptNamespace,
        match="changed before publication acknowledgement",
    ):
        bot.durable_create_receipt_json(path, {"field": 1})

    assert path.read_bytes() == bot.canonical_atomic_json_bytes({"field": True})


def test_regular_receipt_v2_restores_authoritative_post_cycle_histories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted_quote = bot.quote_text_hash("Good quote.")
    stale_quote = "a" * 64
    posted_image = "t01.jpg"
    stale_image = "t02.jpg"
    receipt = valid_regular_receipt_v2(
        quote_history_after=[posted_quote],
        image_history_after=[posted_image],
    )
    lines_used = {posted_quote, stale_quote}
    images_used = {posted_image, stale_image}
    state: dict = {}
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)

    bot.apply_regular_post_receipt(receipt, lines_used, images_used, state)

    assert lines_used == {posted_quote}
    assert images_used == {posted_image}

    # Reconciliation is idempotent and cannot restore the pre-reset histories.
    bot.apply_regular_post_receipt(receipt, lines_used, images_used, state)
    assert lines_used == {posted_quote}
    assert images_used == {posted_image}


@pytest.mark.parametrize("schema_version", [2, 3])
def test_stale_regular_receipt_cannot_erase_newer_cycle_histories(
    monkeypatch: pytest.MonkeyPatch,
    schema_version: int,
) -> None:
    stale_quote = bot.quote_text_hash("Good quote.")
    stale_image = "t01.jpg"
    newer_quote = "b" * 64
    newer_image = "t02.jpg"
    receipt = valid_regular_receipt_v2(
        schema_version=schema_version,
        quote_history_after=[stale_quote],
        image_history_after=[stale_image],
        **(
            {
                "next_meme_post_epoch": 0,
                "meme_schedule_version": 0,
                "meme_schedule_changed_by_quote": False,
            }
            if schema_version == 3
            else {}
        ),
    )
    assert bot.regular_post_receipt_is_semantically_valid(receipt)
    lines_used = {stale_quote, newer_quote}
    images_used = {stale_image, newer_image}
    newer_quote_epoch = int(receipt["quote_post_epoch"]) + 10_000
    state = {
        "last_main_post_id": "950002",
        "last_quote_post_epoch": newer_quote_epoch,
        "last_regular_image_filename": newer_image,
        "next_quote_post_epoch": newer_quote_epoch + 7_200,
    }
    monkeypatch.setattr(
        bot,
        "cache_tweet",
        lambda *_args, **_kwargs: pytest.fail(
            "stale receipt must not recache an older post as current"
        ),
    )
    monkeypatch.setattr(
        bot,
        "record_recent_own_post",
        lambda *_args, **_kwargs: pytest.fail(
            "stale receipt must not move an older post to the recent head"
        ),
    )

    bot.apply_regular_post_receipt(receipt, lines_used, images_used, state)

    assert lines_used == {stale_quote, newer_quote}
    assert images_used == {stale_image, newer_image}
    assert state["last_main_post_id"] == "950002"
    assert state["last_quote_post_epoch"] == newer_quote_epoch
    assert state["next_quote_post_epoch"] == newer_quote_epoch + 7_200


def test_stale_meme_receipt_replay_preserves_newer_daily_guard_and_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_epoch = 1_800_000_100
    stale_next = 1_800_115_200
    newer_epoch = 1_800_200_000
    newer_next = 1_800_300_000
    receipt = {
        "schema_version": 2,
        "post_id": "970001",
        "meme_basename": "001_meme.png",
        "meme_post_epoch": stale_epoch,
        "next_meme_post_epoch": stale_next,
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "next_meme_schedule_mode": "fallback",
        "text": bot.MEME_POST_TEXT,
    }
    assert bot.meme_post_receipt_is_semantically_valid(receipt)
    receipt_path = tmp_path / "meme_post_receipt.json"
    bot.atomic_write_json(receipt_path, receipt)
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", receipt_path)
    monkeypatch.setattr(
        bot,
        "verify_lane_transport_source_lineage_if_present",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bot,
        "retire_lane_transport_journal_if_present",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(
        bot,
        "remove_meme_post_receipt",
        lambda _receipt: receipt_path.unlink(),
    )
    monkeypatch.setattr(
        bot,
        "cache_tweet",
        lambda *_args, **_kwargs: pytest.fail(
            "stale meme receipt must not recache an older post as current"
        ),
    )
    monkeypatch.setattr(
        bot,
        "record_recent_own_post",
        lambda *_args, **_kwargs: pytest.fail(
            "stale meme receipt must not move an older post to the recent head"
        ),
    )
    state = {
        "last_main_post_id": "970002",
        "last_meme_post_epoch": newer_epoch,
        "next_meme_post_epoch": newer_next,
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "next_meme_schedule_mode": "fallback_future",
        "next_meme_schedule_date": bot.epoch_date_str(newer_next),
        "meme_anchor_quote_post_epoch": 123,
        "posted_meme_filenames": ["002_meme.png"],
    }
    current_date = bot.epoch_date_str(newer_epoch)
    assert bot.meme_posted_on_date(state, current_date)

    assert bot.reconcile_meme_post_receipt(state) is True

    assert state["last_main_post_id"] == "970002"
    assert state["last_meme_post_epoch"] == newer_epoch
    assert state["next_meme_post_epoch"] == newer_next
    assert state["next_meme_schedule_mode"] == "fallback_future"
    assert state["meme_anchor_quote_post_epoch"] == 123
    assert state["posted_meme_filenames"] == [
        "001_meme.png",
        "002_meme.png",
    ]
    assert bot.meme_posted_on_date(state, current_date)
    assert newer_epoch < newer_next
    assert not receipt_path.exists()


def test_regular_receipt_v1_remains_backward_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = valid_regular_receipt()
    lines_used = {"a" * 64}
    images_used = {"t02.jpg"}
    state: dict = {}
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)

    assert bot.regular_post_receipt_is_semantically_valid(receipt)
    bot.apply_regular_post_receipt(receipt, lines_used, images_used, state)

    assert lines_used == {"a" * 64, str(receipt["quote_hash"])}
    assert images_used == {"t02.jpg", str(receipt["image_basename"])}


@pytest.mark.parametrize(
    "patch",
    [
        {"quote_history_after": []},
        {"quote_history_after": ["not-a-hash"]},
        {"quote_history_after": ["a" * 64, "a" * 64]},
        {"image_history_after": []},
        {"image_history_after": ["../t01.jpg"]},
        {"image_history_after": ["t01.jpg", "t01.jpg"]},
    ],
)
def test_regular_receipt_v2_rejects_invalid_authoritative_histories(
    patch: dict,
) -> None:
    receipt = valid_regular_receipt_v2(**patch)

    assert bot.regular_post_receipt_is_semantically_valid(receipt) is False


@pytest.mark.parametrize(
    ("image_basename", "initial_count", "expected_count"),
    [
        ("tg_" + ("a" * 64) + ".png", 2, 0),
        ("t01.jpg", 0, 1),
    ],
)
def test_regular_receipt_reconciliation_updates_generated_spacing_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    image_basename: str,
    initial_count: int,
    expected_count: int,
) -> None:
    receipt_file = tmp_path / "regular_post_receipt.json"
    receipt = valid_regular_receipt(image_basename=image_basename)
    bot.atomic_write_json(receipt_file, receipt)
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot, "save_regular_post_protected_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 2)
    caplog.set_level(logging.INFO, logger=bot.log.name)
    lines_used: set[str] = set()
    images_used: set[str] = set()
    state = {"original_regular_posts_since_generated_image": initial_count}

    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is True
    assert state["original_regular_posts_since_generated_image"] == expected_count
    assert image_basename in images_used
    assert not receipt_file.exists()
    assert "GENERATED_IMAGE_SPACING_STATE_UPDATED" in caplog.text

    caplog.clear()
    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is False
    assert state["original_regular_posts_since_generated_image"] == expected_count
    assert "GENERATED_IMAGE_SPACING_STATE_UPDATED" not in caplog.text


def test_regular_receipt_reapply_does_not_double_increment_original_spacing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    receipt = valid_regular_receipt(image_basename="t01.jpg")
    lines_used: set[str] = set()
    images_used: set[str] = set()
    state = {
        "last_quote_post_epoch": receipt["quote_post_epoch"],
        "last_regular_image_filename": "t01.jpg",
        "original_regular_posts_since_generated_image": 1,
    }
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.apply_regular_post_receipt(receipt, lines_used, images_used, state)

    assert state["original_regular_posts_since_generated_image"] == 1
    assert "GENERATED_IMAGE_SPACING_STATE_UPDATED" not in caplog.text


def test_regular_receipt_reapply_initialises_missing_spacing_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_basename = "tg_" + ("a" * 64) + ".png"
    receipt = valid_regular_receipt(image_basename=image_basename)
    lines_used: set[str] = set()
    images_used: set[str] = set()
    state = {
        "last_quote_post_epoch": receipt["quote_post_epoch"],
        "last_regular_image_filename": image_basename,
    }
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)

    bot.apply_regular_post_receipt(receipt, lines_used, images_used, state)

    assert state["original_regular_posts_since_generated_image"] == 0


def test_regular_receipt_reapply_corrects_generated_spacing_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_basename = "tg_" + ("a" * 64) + ".png"
    receipt = valid_regular_receipt(image_basename=image_basename)
    lines_used: set[str] = set()
    images_used: set[str] = set()
    state = {
        "last_quote_post_epoch": receipt["quote_post_epoch"],
        "last_regular_image_filename": image_basename,
        "original_regular_posts_since_generated_image": 2,
    }
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)

    bot.apply_regular_post_receipt(receipt, lines_used, images_used, state)

    assert state["original_regular_posts_since_generated_image"] == 0


@pytest.mark.parametrize("failure", ["save_state", "quote_history", "image_history"])
def test_confirmed_regular_post_receipt_recovers_local_persistence_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    lines_used, images_used, state, lines_used_file, images_used_file, receipt_file, lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    quote_hash = bot.quote_text_hash("Good quote.")
    original_save_state = bot.save_state
    original_save_quote = bot.save_quote_used_hashes
    original_save_image = bot.save_image_used_basenames

    if failure == "save_state":
        monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: (_ for _ in ()).throw(OSError("state write failed")))
    elif failure == "quote_history":
        monkeypatch.setattr(bot, "save_quote_used_hashes", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("quote history failed")))
        monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    else:
        monkeypatch.setattr(bot, "save_image_used_basenames", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("image history failed")))
        monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    # The confirmed journal and receipt remain a global barrier until the
    # protected local transition is replayed; no unrelated lane may overtake
    # this locally recoverable transaction.
    assert bot.ambiguous_remote_post_is_blocking() is True
    assert receipt_file.exists()
    assert quote_hash in lines_used
    assert "t01.jpg" in images_used

    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None if failure != "save_state" else original_save_state(state, **kwargs))
    monkeypatch.setattr(bot, "save_quote_used_hashes", original_save_quote)
    monkeypatch.setattr(bot, "save_image_used_basenames", original_save_image)

    reconciled_lines: set[str] = set()
    reconciled_images: set[str] = set()
    reconciled_state: dict = {}
    assert bot.reconcile_regular_post_receipt(reconciled_lines, reconciled_images, reconciled_state) is True
    assert bot.ambiguous_remote_post_is_blocking() is False
    assert not receipt_file.exists()
    assert json.loads(lines_used_file.read_text(encoding="utf-8")) == [quote_hash]
    assert json.loads(images_used_file.read_text(encoding="utf-8")) == ["t01.jpg"]
    assert quote_hash in reconciled_lines
    assert "t01.jpg" in reconciled_images

    lines_file.write_text("Good quote.\nNew quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(["Good quote.", "New quote."]))
    candidates = bot.quote_candidates_for_current_cycle(reconciled_lines)
    assert [candidate["text"] for candidate in candidates] == ["New quote."]


def test_receipt_write_failure_leaves_confirmed_assets_marked_in_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    quote_hash = bot.quote_text_hash("Good quote.")
    monkeypatch.setattr(bot, "write_regular_post_receipt", lambda receipt: (_ for _ in ()).throw(OSError("receipt failed")))

    with pytest.raises(
        bot.ConfirmedPostLocalPersistenceError,
        match="pending-schedule receipt remains",
    ) as caught:
        bot.post_random_quote(lines_used, images_used, state)

    assert type(caught.value) is bot.ConfirmedPostLocalPersistenceError
    assert bot.ambiguous_remote_post_is_blocking() is True
    assert quote_hash not in lines_used
    assert "t01.jpg" not in images_used
    status, pending = bot.load_regular_post_receipt()
    assert status == "pending_schedule"
    assert pending is not None and pending["post_id"] == "950001"


def test_confirmed_regular_post_sigint_is_delivered_only_after_durable_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        lines_used,
        images_used,
        state,
        _lines_used_file,
        _images_used_file,
        receipt_file,
        _lines_file,
    ) = configure_simple_quote_post(tmp_path, monkeypatch)
    stages: list[str] = []
    original_write = bot.write_regular_post_receipt

    guard_token = object()

    def begin_deferral() -> object:
        stages.append("begin")
        return guard_token

    def write_receipt(receipt: dict) -> None:
        stages.append("receipt")
        original_write(receipt)

    def deliver_pending_sigint(guard: object | None) -> None:
        assert guard is guard_token
        assert receipt_file.exists()
        stages.append("end")
        raise KeyboardInterrupt

    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", begin_deferral)
    monkeypatch.setattr(bot, "write_regular_post_receipt", write_receipt)
    monkeypatch.setattr(bot, "end_confirmed_post_sigint_deferral", deliver_pending_sigint)

    with pytest.raises(KeyboardInterrupt):
        bot.post_random_quote(lines_used, images_used, state)

    assert stages == ["begin", "receipt", "end"]
    assert json.loads(receipt_file.read_text(encoding="utf-8"))["post_id"] == "950001"
    assert bot.ambiguous_remote_post_is_blocking() is True


def test_regular_hard_death_after_remote_acceptance_leaves_restart_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    (
        lines_used,
        images_used,
        state,
        _lines_used_file,
        _images_used_file,
        receipt_file,
        _lines_file,
    ) = configure_simple_quote_post(tmp_path, monkeypatch)
    remote_acceptance = tmp_path / "remote-accepted"

    def accept_then_hard_exit(
        method: str,
        path: str,
        **_kwargs: object,
    ) -> dict:
        assert method == "POST"
        assert path == "/2/tweets"
        assert receipt_file.exists()
        status, attempt = bot.load_regular_post_receipt()
        assert status == "sending"
        assert attempt is not None
        assert attempt["lifecycle_state"] == "attempting"
        descriptor = os.open(
            remote_acceptance,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, b"accepted")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os._exit(73)

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(monkeypatch, accept_then_hard_exit)
    context = multiprocessing.get_context("fork")
    process = context.Process(
        target=bot.post_random_quote,
        args=(lines_used, images_used, state),
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 73
    assert remote_acceptance.read_bytes() == b"accepted"

    status, attempt = bot.load_regular_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lane"] == "quote_image"
    assert attempt["lifecycle_state"] == "attempting"
    assert attempt["selected_identity"] == {
        "quote_hash": bot.quote_text_hash("Good quote."),
        "line_no": 0,
        "source_line_number": 1,
        "image_basename": "t01.jpg",
        "image_no": 0,
    }
    assert attempt["recovery_plan"]["quote_delay_seconds"] in range(
        bot.POST_SLEEP_MIN,
        bot.POST_SLEEP_MAX + 1,
    )
    assert attempt["recovery_plan"]["quote_history_after"] == [
        bot.quote_text_hash("Good quote."),
    ]
    assert attempt["recovery_plan"]["image_history_after"] == ["t01.jpg"]
    assert bot.ambiguous_remote_post_is_blocking() is True

    retry_calls = 0

    def forbidden_retry(**_kwargs: object) -> dict:
        nonlocal retry_calls
        retry_calls += 1
        return {"data": {"id": "950002"}}

    monkeypatch.setattr(bot, "create_post", forbidden_retry)
    for _restart in range(2):
        with pytest.raises(bot.AmbiguousRemotePostOutcome):
            bot.post_random_quote(set(), set(), {})
    assert retry_calls == 0


@pytest.mark.parametrize("reset_lane", ["quote_history", "image_history"])
def test_regular_hard_death_preserves_exact_post_reset_cycle_histories(
    reset_lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    (
        lines_used,
        images_used,
        state,
        _lines_used_file,
        _images_used_file,
        _receipt_file,
        _lines_file,
    ) = configure_simple_quote_post(tmp_path, monkeypatch)
    quote_hash = bot.quote_text_hash("Good quote.")
    if reset_lane == "quote_history":
        lines_used.update({quote_hash, "a" * 64})
    else:
        image_dir = tmp_path / "images"
        second_image = image_dir / "t02.jpg"
        second_image.write_bytes(b"second")
        monkeypatch.setattr(
            bot,
            "load_image_analysis",
            lambda: image_analysis_for_paths(
                [image_dir / "t01.jpg", second_image],
            ),
        )
        monkeypatch.setattr(
            bot.random,
            "choice",
            lambda choices: sorted(
                choices,
                key=lambda choice: str(
                    choice.get("basename")
                    if isinstance(choice, dict)
                    else choice
                ),
            )[0],
        )
        images_used.update({"t01.jpg", "t02.jpg"})

    def accept_then_hard_exit(
        _method: str,
        _path: str,
        **_kwargs: object,
    ) -> dict:
        os._exit(75)

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(monkeypatch, accept_then_hard_exit)
    process = multiprocessing.get_context("fork").Process(
        target=bot.post_random_quote,
        args=(lines_used, images_used, state),
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 75

    status, attempt = bot.load_regular_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lifecycle_state"] == "attempting"
    assert attempt["recovery_plan"]["quote_history_after"] == [quote_hash]
    assert attempt["recovery_plan"]["image_history_after"] == ["t01.jpg"]
    assert "a" * 64 not in attempt["recovery_plan"]["quote_history_after"]
    assert "t02.jpg" not in attempt["recovery_plan"]["image_history_after"]


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
@pytest.mark.parametrize(
    "boundary",
    [
        "pre_request",
        "response_before_confirmed_promotion",
        "confirmed_promotion_before_state",
        "full_receipt_before_state",
        "state_before_receipt_retirement",
    ],
)
def test_main_post_hard_death_boundaries_never_recreate_remote_post(
    lane: str,
    boundary: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    if lane == "quote_image":
        lines_used, images_used, state, *_paths = configure_simple_quote_post(
            tmp_path,
            monkeypatch,
        )
        receipt_path = bot.REGULAR_POST_RECEIPT_FILE
        target = lambda: bot.post_random_quote(lines_used, images_used, state)
        original_write = bot.write_regular_post_receipt
        original_remove = bot.remove_regular_post_receipt
        confirmed_post_id = "950001"
    else:
        state, receipt_path = configure_simple_meme_post(tmp_path, monkeypatch)
        target = lambda: bot.post_next_meme(state)
        original_write = bot.write_meme_post_receipt
        original_remove = bot.remove_meme_post_receipt
        confirmed_post_id = "970001"

    original_promote = bot.promote_main_post_attempt_to_confirmed_pending_schedule
    original_finalize = bot.finalize_confirmed_pending_schedule_receipt

    remote_acceptance = tmp_path / f"{lane}-accepted-{boundary}"

    def confirmed_remote_request(
        method: str,
        path: str,
        **_kwargs: object,
    ) -> dict:
        assert method == "POST"
        assert path == "/2/tweets"
        descriptor = os.open(
            remote_acceptance,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, b"accepted")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return {"data": {"id": confirmed_post_id}}

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(monkeypatch, confirmed_remote_request)
    monkeypatch.setattr(
        bot,
        "enqueue_historical_context_obligation",
        lambda _receipt: None,
    )
    monkeypatch.setattr(
        bot,
        "safely_process_due_historical_context_obligations",
        lambda **_kwargs: [],
    )

    if boundary == "pre_request":
        monkeypatch.setattr(
            bot,
            "begin_confirmed_post_sigint_deferral",
            lambda: os._exit(76),
        )
        expected_exit = 76
    elif boundary == "response_before_confirmed_promotion":
        monkeypatch.setattr(
            bot,
            "promote_main_post_attempt_to_confirmed_pending_schedule",
            lambda *_args, **_kwargs: os._exit(77),
        )
        expected_exit = 77
    elif boundary == "confirmed_promotion_before_state":
        def promote_then_exit(*args: object, **kwargs: object) -> None:
            original_promote(*args, **kwargs)
            os._exit(78)

        monkeypatch.setattr(
            bot,
            "promote_main_post_attempt_to_confirmed_pending_schedule",
            promote_then_exit,
        )
        expected_exit = 78
    elif boundary == "full_receipt_before_state":
        def finalise_then_exit(*args: object, **kwargs: object) -> None:
            original_finalize(*args, **kwargs)
            os._exit(80)

        monkeypatch.setattr(
            bot,
            "finalize_confirmed_pending_schedule_receipt",
            finalise_then_exit,
        )
        expected_exit = 80
    else:
        if lane == "quote_image":
            monkeypatch.setattr(
                bot,
                "write_regular_post_receipt",
                original_write,
            )
            monkeypatch.setattr(
                bot,
                "remove_regular_post_receipt",
                lambda *_args, **_kwargs: os._exit(79),
            )
        else:
            monkeypatch.setattr(
                bot,
                "write_meme_post_receipt",
                original_write,
            )
            monkeypatch.setattr(
                bot,
                "remove_meme_post_receipt",
                lambda *_args, **_kwargs: os._exit(79),
            )
        expected_exit = 79

    process = multiprocessing.get_context("fork").Process(target=target)
    process.start()
    process.join(timeout=10)
    assert process.exitcode == expected_exit
    assert receipt_path.exists()

    status, receipt = (
        bot.load_regular_post_receipt()
        if lane == "quote_image"
        else bot.load_meme_post_receipt()
    )
    assert receipt is not None
    if boundary == "pre_request":
        assert not remote_acceptance.exists()
        assert status == "sending"
        assert receipt["lifecycle_state"] == "attempting"
    elif boundary == "response_before_confirmed_promotion":
        assert remote_acceptance.read_bytes() == b"accepted"
        assert status == "sending"
        assert receipt["lifecycle_state"] == "attempting"
    elif boundary == "confirmed_promotion_before_state":
        assert remote_acceptance.read_bytes() == b"accepted"
        assert status == "pending_schedule"
        assert receipt["post_id"] == confirmed_post_id
    else:
        assert remote_acceptance.read_bytes() == b"accepted"
        assert status == "valid"
        assert receipt["post_id"] == confirmed_post_id

    remote_recreates = 0

    def forbidden_recreate(*_args: object, **_kwargs: object) -> object:
        nonlocal remote_recreates
        remote_recreates += 1
        raise AssertionError("restart must not recreate a remote post")

    monkeypatch.setattr(bot, "create_post", forbidden_recreate)
    if status == "sending":
        assert bot.ambiguous_remote_post_is_blocking() is True
        if lane == "quote_image":
            assert bot.reconcile_regular_post_receipt(set(), set(), {}) is False
        else:
            assert bot.reconcile_meme_post_receipt({}) is False
    else:
        if lane == "quote_image":
            monkeypatch.setattr(
                bot,
                "write_regular_post_receipt",
                original_write,
            )
            monkeypatch.setattr(
                bot,
                "remove_regular_post_receipt",
                original_remove,
            )
            assert bot.reconcile_regular_post_receipt(set(), set(), {}) is True
        else:
            monkeypatch.setattr(
                bot,
                "write_meme_post_receipt",
                original_write,
            )
            monkeypatch.setattr(
                bot,
                "remove_meme_post_receipt",
                original_remove,
            )
            assert bot.reconcile_meme_post_receipt({}) is True
        assert not receipt_path.exists()
    assert remote_recreates == 0


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_main_attempt_atomic_exchange_interruption_leaves_old_or_new_valid_json(
    lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if lane == "quote_image":
        quote_hash = bot.quote_text_hash("Good quote.")
        attempt = bot.build_main_post_attempt(
            lane=lane,
            text="Good quote.",
            media_ids=["media-1"],
            made_with_ai=False,
            selected_identity={
                "quote_hash": quote_hash,
                "line_no": 0,
                "source_line_number": 1,
                "image_basename": "t01.jpg",
                "image_no": 0,
            },
            recovery_plan={
                "quote_delay_seconds": bot.POST_SLEEP_MIN,
                "meme_delay_seconds": None,
                "meme_scheduling_enabled": False,
                "meme_trigger_after_hour": int(bot.MEME_TRIGGER_AFTER_HOUR),
                "meme_schedule_version": int(bot.MEME_SCHEDULE_VERSION),
                "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
                "meme_schedule_before": bot.bound_meme_schedule_state(
                    {},
                    schedule_timezone=bot.MAIN_POST_SCHEDULE_TIMEZONE,
                ),
                "quote_history_after": [quote_hash],
                "image_history_after": ["t01.jpg"],
            },
        )
        receipt_path = bot.REGULAR_POST_RECEIPT_FILE
        loader = bot.load_regular_post_receipt
    else:
        attempt = bot.build_main_post_attempt(
            lane=lane,
            text=bot.MEME_POST_TEXT,
            media_ids=["media-1"],
            made_with_ai=False,
            selected_identity={"meme_basename": "001_meme.png"},
            recovery_plan={
                "next_schedule_mode": "fallback",
                "meme_schedule_version": int(bot.MEME_SCHEDULE_VERSION),
                "fallback_hour": int(bot.MEME_FALLBACK_HOUR),
                "fallback_minute": int(bot.MEME_FALLBACK_MINUTE),
                "image_summary": "Unit meme image.",
                "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
            },
        )
        receipt_path = bot.MEME_POST_RECEIPT_FILE
        loader = bot.load_meme_post_receipt
    bot.write_main_post_attempt(attempt)
    old_bytes = receipt_path.read_bytes()
    real_exchange = transport_journal_module._rename_exchange

    def exit_before_exchange(
        _directory_fd: int,
        _first: str,
        _second: str,
    ) -> None:
        os._exit(82)

    monkeypatch.setattr(
        transport_journal_module,
        "_rename_exchange",
        exit_before_exchange,
    )
    process = multiprocessing.get_context("fork").Process(
        target=bot.mark_main_post_attempt_attempting,
        args=(attempt,),
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 82
    assert receipt_path.read_bytes() == old_bytes
    assert loader() == ("sending", attempt)
    staging = [
        path
        for path in tmp_path.iterdir()
        if path.name.startswith(transport_journal_module.JOURNAL_STAGING_PREFIX)
    ]
    assert len(staging) == 1
    staging[0].unlink()

    def exit_after_exchange(
        directory_fd: int,
        first: str,
        second: str,
    ) -> None:
        real_exchange(directory_fd, first, second)
        os._exit(83)

    monkeypatch.setattr(
        transport_journal_module,
        "_rename_exchange",
        exit_after_exchange,
    )
    process = multiprocessing.get_context("fork").Process(
        target=bot.mark_main_post_attempt_attempting,
        args=(attempt,),
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 83
    status, current = loader()
    assert status == "sending"
    assert current is not None
    assert current["lifecycle_state"] == "attempting"
    assert current["attempt_id"] == attempt["attempt_id"]
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == current
    assert any(
        path.name.startswith(transport_journal_module.JOURNAL_STAGING_PREFIX)
        for path in tmp_path.iterdir()
    )


def schema_current_main_attempt(lane: str) -> dict:
    """Build one current-schema attempt without touching a receipt path."""
    if lane == "quote_image":
        quote_hash = bot.quote_text_hash("Good quote.")
        return bot.build_main_post_attempt(
            lane=lane,
            text="Good quote.",
            media_ids=["media-1"],
            made_with_ai=False,
            selected_identity={
                "quote_hash": quote_hash,
                "line_no": 0,
                "source_line_number": 1,
                "image_basename": "t01.jpg",
                "image_no": 0,
            },
            recovery_plan={
                "quote_delay_seconds": 7200,
                "meme_delay_seconds": 3600,
                "meme_scheduling_enabled": True,
                "meme_trigger_after_hour": 12,
                "meme_schedule_version": 2,
                "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
                "meme_schedule_before": bot.bound_meme_schedule_state(
                    {},
                    schedule_timezone=bot.MAIN_POST_SCHEDULE_TIMEZONE,
                ),
                "quote_history_after": [quote_hash],
                "image_history_after": ["t01.jpg"],
            },
            attempt_epoch=1_800_000_000,
        )
    return bot.build_main_post_attempt(
        lane=lane,
        text=bot.MEME_POST_TEXT,
        media_ids=["media-1"],
        made_with_ai=False,
        selected_identity={"meme_basename": "001_meme.png"},
        recovery_plan={
            "next_schedule_mode": "fallback",
            "meme_schedule_version": 2,
            "fallback_hour": 16,
            "fallback_minute": 0,
            "image_summary": "",
            "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
        },
        attempt_epoch=1_800_000_000,
    )


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_main_sending_to_attempting_rejects_same_bytes_new_inode_race(
    lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = schema_current_main_attempt(lane)
    receipt_path = (
        bot.REGULAR_POST_RECEIPT_FILE
        if lane == "quote_image"
        else bot.MEME_POST_RECEIPT_FILE
    )
    bot.write_main_post_attempt(attempt)
    original_bytes = receipt_path.read_bytes()
    original_inode = receipt_path.stat().st_ino
    raced_inode: int | None = None
    real_replace_generation = (
        transport_journal_module.replace_exact_source_receipt_generation
    )

    def inject_same_bytes_new_inode(
        path: Path,
        **kwargs: object,
    ) -> None:
        nonlocal raced_inode
        assert Path(path) == receipt_path
        assert kwargs["expected_bytes"] == original_bytes
        peer = tmp_path / f"{lane}-same-bytes-peer.json"
        peer.write_bytes(original_bytes)
        peer.chmod(transport_journal_module.JOURNAL_MODE)
        raced_inode = peer.stat().st_ino
        assert raced_inode != original_inode
        os.replace(peer, receipt_path)
        directory_fd = os.open(
            tmp_path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        real_replace_generation(path, **kwargs)

    monkeypatch.setattr(
        transport_journal_module,
        "replace_exact_source_receipt_generation",
        inject_same_bytes_new_inode,
    )

    with pytest.raises(
        bot.BoundSourceReceiptTransitionError,
        match="replacement did not complete",
    ):
        bot.mark_main_post_attempt_attempting(attempt)

    assert raced_inode is not None
    assert receipt_path.stat().st_ino == raced_inode
    assert receipt_path.read_bytes() == original_bytes
    loader = (
        bot.load_regular_post_receipt
        if lane == "quote_image"
        else bot.load_meme_post_receipt
    )
    assert loader() == ("sending", attempt)
    assert any(
        item.name.startswith(transport_journal_module.JOURNAL_STAGING_PREFIX)
        for item in tmp_path.iterdir()
    )


def test_current_meme_attempt_binds_image_summary_for_restart_recovery() -> None:
    summary = "A poster with a concise political slogan."
    attempt = bot.build_main_post_attempt(
        lane="daily_meme",
        text=bot.MEME_POST_TEXT,
        media_ids=["media-1"],
        made_with_ai=False,
        selected_identity={"meme_basename": "001_meme.png"},
        recovery_plan={
            "next_schedule_mode": "fallback",
            "meme_schedule_version": 2,
            "fallback_hour": 16,
            "fallback_minute": 0,
            "image_summary": summary,
            "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
        },
        attempt_epoch=1_800_000_000,
    )
    assert attempt["schema_version"] == 5
    attempting = {**attempt, "lifecycle_state": "attempting"}
    with pytest.raises(RuntimeError):
        bot.build_confirmed_pending_schedule_receipt(
            attempting,
            post_id="970001",
            confirmation_epoch=1_800_000_100,
        )
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempting,
        post_id="970001",
        confirmation_epoch=1_800_000_100,
        image_summary=summary,
    )
    receipt = bot.materialize_bound_meme_schedule_receipt(pending)
    assert receipt["image_summary"] == summary


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_pending_to_full_receipt_atomic_replace_survives_hard_death(
    lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if lane == "quote_image":
        receipt_path = bot.REGULAR_POST_RECEIPT_FILE
        loader = bot.load_regular_post_receipt
        post_id = "950001"
    else:
        receipt_path = bot.MEME_POST_RECEIPT_FILE
        loader = bot.load_meme_post_receipt
        post_id = "970001"

    attempt = schema_current_main_attempt(lane)
    bot.write_main_post_attempt(attempt)
    attempting = bot.mark_main_post_attempt_attempting(attempt)
    payload = bot.main_post_attempt_payload(attempting)
    source = bot.bind_lane_transport_source(
        receipt_path=receipt_path,
        receipt=attempting,
        lane=lane,
        payload=payload,
    )
    authority = bot.begin_transport_transaction(
        receipt_path=receipt_path,
        source_binding=source,
    )
    authority = bot.arm_transport_transaction(
        Path(authority.journal_path),
        authority,
        mutation_authority=bot.transaction_mutation_authority(
            "focused transport arming"
        ),
    )
    bot.consume_transport_authority(
        Path(authority.journal_path),
        authority,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
    )
    bot.confirm_transport_transaction(
        Path(authority.journal_path),
        authority,
        mutation_authority=bot.transaction_mutation_authority(
            "focused transport confirmation"
        ),
        post_id=post_id,
        confirmation_epoch=1_800_000_100,
    )
    pending = bot.promote_main_post_attempt_to_confirmed_pending_schedule(
        attempting,
        post_id=post_id,
        confirmation_epoch=1_800_000_100,
    )
    pending_bytes = receipt_path.read_bytes()
    real_replace = os.replace

    def exit_before_replace(source: object, destination: object) -> None:
        if Path(destination) == receipt_path:
            os._exit(84)
        real_replace(source, destination)

    monkeypatch.setattr(bot.os, "replace", exit_before_replace)
    process = multiprocessing.get_context("fork").Process(
        target=bot.finalize_confirmed_pending_schedule_receipt,
        args=(pending,),
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 84
    assert receipt_path.read_bytes() == pending_bytes
    assert loader() == ("pending_schedule", pending)

    def exit_after_replace(source: object, destination: object) -> None:
        real_replace(source, destination)
        if Path(destination) == receipt_path:
            os._exit(85)

    monkeypatch.setattr(bot.os, "replace", exit_after_replace)
    process = multiprocessing.get_context("fork").Process(
        target=bot.finalize_confirmed_pending_schedule_receipt,
        args=(pending,),
    )
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 85
    status, full_receipt = loader()
    assert status == "valid"
    assert full_receipt is not None
    assert full_receipt["post_id"] == post_id


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_pending_schedule_plan_survives_current_configuration_change(
    lane: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = schema_current_main_attempt(lane)
    attempt["lifecycle_state"] = "attempting"
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempt,
        post_id="950001" if lane == "quote_image" else "970001",
        confirmation_epoch=1_800_000_100,
    )

    monkeypatch.setattr(bot, "POST_SLEEP_MIN", 1)
    monkeypatch.setattr(bot, "POST_SLEEP_MAX", 2)
    monkeypatch.setattr(bot, "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS", 1)
    monkeypatch.setattr(bot, "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS", 2)
    monkeypatch.setattr(bot, "MEME_FALLBACK_HOUR", 17)
    monkeypatch.setattr(bot, "MEME_FALLBACK_MINUTE", 30)
    monkeypatch.setattr(bot, "MEME_SCHEDULE_VERSION", 3)

    assert bot.main_post_attempt_is_semantically_valid(attempt) is True
    assert bot.confirmed_pending_schedule_receipt_is_semantically_valid(
        pending,
        expected_lane=lane,
    )
    if lane == "quote_image":
        receipt = bot.materialize_bound_regular_schedule_receipt(pending)
        assert receipt["next_quote_post_epoch"] == 1_800_007_300
        assert receipt["next_meme_post_epoch"] == 0
        lines_used: set[str] = set()
        images_used: set[str] = set()
        state: dict = {}
        bot.apply_regular_post_receipt(
            receipt,
            lines_used,
            images_used,
            state,
        )
    else:
        receipt = bot.materialize_bound_meme_schedule_receipt(pending)
        next_dt = datetime.fromtimestamp(receipt["next_meme_post_epoch"])
        assert (next_dt.hour, next_dt.minute) == (16, 0)
        state = {}
        bot.apply_meme_post_receipt(receipt, state)
    expected_schedule_version = 0 if lane == "quote_image" else 2
    assert receipt["meme_schedule_version"] == expected_schedule_version
    assert state["meme_schedule_version"] == expected_schedule_version


@pytest.mark.parametrize(
    ("lane", "confirmation_local"),
    [
        (
            "quote_image",
            datetime(
                2026,
                3,
                29,
                12,
                30,
                tzinfo=ZoneInfo(bot.MAIN_POST_SCHEDULE_TIMEZONE),
            ),
        ),
        (
            "quote_image",
            datetime(
                2026,
                10,
                25,
                12,
                30,
                tzinfo=ZoneInfo(bot.MAIN_POST_SCHEDULE_TIMEZONE),
            ),
        ),
        (
            "daily_meme",
            datetime(
                2026,
                3,
                28,
                23,
                30,
                tzinfo=ZoneInfo(bot.MAIN_POST_SCHEDULE_TIMEZONE),
            ),
        ),
        (
            "daily_meme",
            datetime(
                2026,
                10,
                24,
                23,
                30,
                tzinfo=ZoneInfo(bot.MAIN_POST_SCHEDULE_TIMEZONE),
            ),
        ),
    ],
)
def test_current_pending_schedule_replay_ignores_ambient_timezone_across_dst(
    lane: str,
    confirmation_local: datetime,
) -> None:
    """Identical pending bytes have one Europe/London calendar result."""

    if not hasattr(time, "tzset"):
        pytest.skip("ambient timezone switching requires time.tzset")
    confirmation_epoch = int(confirmation_local.timestamp())
    attempt = schema_current_main_attempt(lane)
    attempt["attempt_epoch"] = confirmation_epoch - 60
    attempt["lifecycle_state"] = "attempting"
    assert attempt["schema_version"] == 5
    assert bot.main_post_attempt_is_semantically_valid(attempt)
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempt,
        post_id="950001" if lane == "quote_image" else "970001",
        confirmation_epoch=confirmation_epoch,
        image_summary=str(attempt["recovery_plan"].get("image_summary") or ""),
    )
    pending_bytes = bot.canonical_atomic_json_bytes(pending)
    original_timezone = os.environ.get("TZ")
    outputs: list[dict] = []
    applied_schedules: list[tuple[object, ...]] = []
    try:
        for ambient_timezone in ("UTC", "Pacific/Honolulu", "Asia/Tokyo"):
            os.environ["TZ"] = ambient_timezone
            time.tzset()
            assert bot.canonical_atomic_json_bytes(pending) == pending_bytes
            if lane == "quote_image":
                receipt = bot.materialize_bound_regular_schedule_receipt(
                    copy.deepcopy(pending)
                )
                assert bot.regular_post_receipt_is_semantically_valid(receipt)
                state: dict = {}
                bot.apply_regular_post_receipt(receipt, set(), set(), state)
            else:
                receipt = bot.materialize_bound_meme_schedule_receipt(
                    copy.deepcopy(pending)
                )
                assert bot.meme_post_receipt_is_semantically_valid(receipt)
                state = {}
                bot.apply_meme_post_receipt(receipt, state)
            reloaded_state = json.loads(bot.canonical_atomic_json_bytes(state))
            assert bot.validate_meme_schedule_state(
                reloaded_state,
                path=Path("reloaded-timezone-bound-state.json"),
            )
            outputs.append(receipt)
            applied_schedules.append(
                (
                    state.get("next_meme_post_epoch"),
                    state.get("meme_schedule_version"),
                    state.get("next_meme_schedule_mode"),
                    state.get("next_meme_schedule_date"),
                    state.get("meme_anchor_quote_post_epoch"),
                )
            )
    finally:
        if original_timezone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_timezone
        time.tzset()

    assert outputs[1:] == [outputs[0], outputs[0]]
    assert applied_schedules[1:] == [applied_schedules[0], applied_schedules[0]]
    if lane == "quote_image":
        assert outputs[0]["meme_schedule_changed_by_quote"] is True
        assert outputs[0]["next_meme_post_epoch"] == confirmation_epoch + 3600
        assert outputs[0]["next_meme_schedule_date"] == (
            confirmation_local.strftime("%Y-%m-%d")
        )
    else:
        expected_next = (confirmation_local + timedelta(days=1)).replace(
            hour=16,
            minute=0,
            second=0,
            microsecond=0,
        )
        assert outputs[0]["next_meme_post_epoch"] == int(
            expected_next.timestamp()
        )
        assert outputs[0]["next_meme_schedule_date"] == expected_next.strftime(
            "%Y-%m-%d"
        )


def test_meme_schedule_producers_and_guards_ignore_ambient_timezone() -> None:
    """Current meme calendar state has one production-zone interpretation."""

    if not hasattr(time, "tzset"):
        pytest.skip("ambient timezone switching requires time.tzset")
    quote_local = datetime(
        2026,
        3,
        29,
        16,
        30,
        tzinfo=ZoneInfo(bot.MAIN_POST_SCHEDULE_TIMEZONE),
    )
    quote_epoch = int(quote_local.timestamp())
    original_timezone = os.environ.get("TZ")
    outputs: list[tuple[dict, dict, bool]] = []
    try:
        for ambient_timezone in ("UTC", "Pacific/Honolulu", "Asia/Tokyo"):
            os.environ["TZ"] = ambient_timezone
            time.tzset()
            fallback = bot.next_meme_schedule_fields({}, quote_epoch)
            after_quote = bot.meme_schedule_fields_after_quote_post(
                {},
                quote_epoch,
                delay=3600,
            )
            reloaded = json.loads(bot.canonical_atomic_json_bytes(after_quote))
            assert bot.validate_meme_schedule_state(
                reloaded,
                path=Path("reloaded-produced-meme-state.json"),
            )
            posted_on_bound_date = bot.meme_posted_on_date(
                {"last_meme_post_epoch": quote_epoch},
                quote_local.strftime("%Y-%m-%d"),
            )
            outputs.append((fallback, after_quote, posted_on_bound_date))
    finally:
        if original_timezone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_timezone
        time.tzset()

    assert outputs[1:] == [outputs[0], outputs[0]]
    assert outputs[0][2] is True
    assert outputs[0][1]["next_meme_schedule_date"] == "2026-03-29"


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_confirmed_pending_schedule_requires_timezone_bound_schema_v5(
    lane: str,
) -> None:
    legacy = schema_current_main_attempt(lane)
    legacy["schema_version"] = 4
    legacy["lifecycle_state"] = "attempting"
    legacy["recovery_plan"].pop("schedule_timezone")
    assert bot.main_post_attempt_is_semantically_valid(legacy)
    pending = {
        "schema_version": 1,
        "receipt_type": "confirmed_pending_schedule",
        "post_id": "950001" if lane == "quote_image" else "970001",
        "confirmation_epoch": int(legacy["attempt_epoch"]) + 1,
        "source_attempt": legacy,
        "image_summary": str(legacy["recovery_plan"].get("image_summary") or ""),
    }
    assert not bot.confirmed_pending_schedule_receipt_is_semantically_valid(
        pending,
        expected_lane=lane,
    )
    with pytest.raises(RuntimeError, match="pending-schedule receipt is invalid"):
        bot.build_confirmed_pending_schedule_receipt(
            legacy,
            post_id=pending["post_id"],
            confirmation_epoch=pending["confirmation_epoch"],
            image_summary=pending["image_summary"],
        )


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
@pytest.mark.parametrize("timezone_value", [None, "UTC", 0])
def test_current_main_attempt_rejects_unbound_schedule_timezone(
    lane: str,
    timezone_value: object,
) -> None:
    attempt = schema_current_main_attempt(lane)
    if timezone_value is None:
        attempt["recovery_plan"].pop("schedule_timezone")
    else:
        attempt["recovery_plan"]["schedule_timezone"] = timezone_value
    assert bot.main_post_attempt_is_semantically_valid(attempt) is False


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_main_attempt_builder_refuses_unbound_current_plan(lane: str) -> None:
    """The builder cannot silently downgrade a current plan to a legacy schema."""

    current = schema_current_main_attempt(lane)
    recovery_plan = copy.deepcopy(current["recovery_plan"])
    recovery_plan.pop("schedule_timezone")
    with pytest.raises(ValueError, match="bind the production schedule timezone"):
        bot.build_main_post_attempt(
            lane=lane,
            text=str(current["text"]),
            media_ids=list(current["media_ids"]),
            made_with_ai=bool(current["made_with_ai"]),
            selected_identity=copy.deepcopy(current["selected_identity"]),
            recovery_plan=recovery_plan,
            attempt_epoch=int(current["attempt_epoch"]),
        )


def test_legacy_schema4_schedule_validation_ignores_ambient_timezone() -> None:
    """Reader compatibility uses the historical production calendar."""

    if not hasattr(time, "tzset"):
        pytest.skip("ambient timezone switching requires time.tzset")
    next_local = datetime(
        2026,
        3,
        29,
        0,
        30,
        tzinfo=ZoneInfo(bot.MAIN_POST_SCHEDULE_TIMEZONE),
    )
    next_epoch = int(next_local.timestamp())
    legacy = schema_current_main_attempt("quote_image")
    legacy["schema_version"] = 4
    legacy["recovery_plan"].pop("schedule_timezone")
    legacy["recovery_plan"]["meme_schedule_before"] = {
        "last_meme_post_epoch": 0,
        "next_meme_post_epoch": next_epoch,
        "meme_schedule_version": int(bot.MEME_SCHEDULE_VERSION),
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": "2026-03-29",
        "meme_anchor_quote_post_epoch": 0,
    }
    legacy_bytes = bot.canonical_atomic_json_bytes(legacy)
    original_timezone = os.environ.get("TZ")
    try:
        for ambient_timezone in ("Europe/London", "Pacific/Honolulu", "Asia/Tokyo"):
            os.environ["TZ"] = ambient_timezone
            time.tzset()
            assert bot.canonical_atomic_json_bytes(legacy) == legacy_bytes
            assert bot.main_post_attempt_is_semantically_valid(legacy)
    finally:
        if original_timezone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_timezone
        time.tzset()


def test_current_attempt_schema_rejects_absurd_bound_schedule_values() -> None:
    regular = schema_current_main_attempt("quote_image")
    regular["recovery_plan"]["quote_delay_seconds"] = (
        bot.MAIN_POST_ATTEMPT_MAX_BOUND_DELAY_SECONDS + 1
    )
    assert bot.main_post_attempt_is_semantically_valid(regular) is False

    meme = schema_current_main_attempt("daily_meme")
    meme["recovery_plan"]["fallback_hour"] = 24
    assert bot.main_post_attempt_is_semantically_valid(meme) is False

    future_regular = schema_current_main_attempt("quote_image")
    future_regular["recovery_plan"]["meme_schedule_version"] = (
        bot.MEME_SCHEDULE_VERSION + 1
    )
    assert bot.main_post_attempt_is_semantically_valid(future_regular) is False

    future_snapshot = schema_current_main_attempt("quote_image")
    future_snapshot["recovery_plan"]["meme_schedule_before"][
        "meme_schedule_version"
    ] = bot.MEME_SCHEDULE_VERSION + 1
    assert bot.main_post_attempt_is_semantically_valid(future_snapshot) is False

    future_meme = schema_current_main_attempt("daily_meme")
    future_meme["recovery_plan"]["meme_schedule_version"] = (
        bot.MEME_SCHEDULE_VERSION + 1
    )
    assert bot.main_post_attempt_is_semantically_valid(future_meme) is False


@pytest.mark.parametrize("lane", ("quote_image", "daily_meme"))
def test_main_attempt_requires_integer_schema_version(lane: str) -> None:
    attempt = schema_current_main_attempt(lane)
    for invalid_schema in (float(attempt["schema_version"]), True):
        changed = copy.deepcopy(attempt)
        changed["schema_version"] = invalid_schema
        assert bot.main_post_attempt_is_semantically_valid(changed) is False


@pytest.mark.parametrize("source_line_number", (1.0, True))
def test_main_attempt_requires_integer_source_line_number(
    source_line_number: object,
) -> None:
    attempt = schema_current_main_attempt("quote_image")
    attempt["selected_identity"]["source_line_number"] = source_line_number
    assert bot.main_post_attempt_is_semantically_valid(attempt) is False


@pytest.mark.parametrize(
    ("lane", "expected_schema"),
    (("quote_image", 3), ("daily_meme", 2)),
)
def test_main_attempt_keeps_supported_legacy_integer_schemas(
    lane: str,
    expected_schema: int,
) -> None:
    if lane == "quote_image":
        quote_hash = bot.quote_text_hash("Good quote.")
        attempt = schema_current_main_attempt(lane)
        attempt["schema_version"] = expected_schema
        attempt["recovery_plan"] = {
            "quote_delay_seconds": 7200,
            "meme_delay_seconds": None,
            "quote_history_after": [quote_hash],
            "image_history_after": ["t01.jpg"],
        }
    else:
        attempt = schema_current_main_attempt(lane)
        attempt["schema_version"] = expected_schema
        attempt["recovery_plan"] = {"next_schedule_mode": "fallback"}

    assert type(attempt["schema_version"]) is int
    assert attempt["schema_version"] == expected_schema
    assert bot.main_post_attempt_is_semantically_valid(attempt) is True


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_legacy_main_attempt_is_reader_only_and_cannot_enter_live_transport(
    lane: str,
) -> None:
    legacy = schema_current_main_attempt(lane)
    legacy["schema_version"] = 4
    legacy["recovery_plan"].pop("schedule_timezone")
    path = (
        bot.REGULAR_POST_RECEIPT_FILE
        if lane == "quote_image"
        else bot.MEME_POST_RECEIPT_FILE
    )
    loader = (
        bot.load_regular_post_receipt
        if lane == "quote_image"
        else bot.load_meme_post_receipt
    )

    assert bot.main_post_attempt_is_semantically_valid(legacy)
    assert not bot.current_main_post_attempt_is_semantically_valid(legacy)
    with pytest.raises(RuntimeError, match="current-schema"):
        bot.write_main_post_attempt(legacy)
    assert not path.exists()

    path.write_bytes(bot.canonical_atomic_json_bytes(legacy))
    path.chmod(0o600)
    before = path.read_bytes()
    assert loader() == ("sending", legacy)
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="current-schema"):
        bot.mark_main_post_attempt_attempting(legacy)
    with pytest.raises(bot.TransportJournalError, match="current-schema"):
        bot.prepare_main_tweet_transport(legacy)
    assert path.read_bytes() == before
    assert loader() == ("sending", legacy)


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_attempting_main_attempt_cannot_bypass_single_use_promotion(
    lane: str,
) -> None:
    attempting = schema_current_main_attempt(lane)
    attempting["lifecycle_state"] = "attempting"
    path = (
        bot.REGULAR_POST_RECEIPT_FILE
        if lane == "quote_image"
        else bot.MEME_POST_RECEIPT_FILE
    )

    assert bot.current_main_post_attempt_is_semantically_valid(attempting)
    with pytest.raises(RuntimeError, match="sending main-post attempt"):
        bot.write_main_post_attempt(attempting)
    assert not path.exists()

    path.write_bytes(bot.canonical_atomic_json_bytes(attempting))
    path.chmod(0o600)
    before = path.read_bytes()
    with pytest.raises(
        bot.TransportJournalError,
        match="sending main-post attempt",
    ):
        bot.prepare_main_tweet_transport(attempting)
    assert path.read_bytes() == before
    assert not any(
        os.path.lexists(journal_path)
        for journal_path in bot.remote_write_transport_journal_paths()
    )


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_current_attempt_cannot_bypass_confirmed_pending_schedule_receipt(
    lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regular_path = bot.REGULAR_POST_RECEIPT_FILE
    meme_path = bot.MEME_POST_RECEIPT_FILE
    attempt = schema_current_main_attempt(lane)
    bot.write_main_post_attempt(attempt)
    attempting = bot.mark_main_post_attempt_attempting(attempt)
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempting,
        post_id="950001" if lane == "quote_image" else "970001",
        confirmation_epoch=1_800_000_100,
    )
    if lane == "quote_image":
        full = bot.materialize_bound_regular_schedule_receipt(pending)
        with pytest.raises(
            bot.UnresolvedRegularPostReceipt,
            match="pending-schedule",
        ):
            bot.write_regular_post_receipt(full)
        assert bot.load_regular_post_receipt() == ("sending", attempting)
    else:
        full = bot.materialize_bound_meme_schedule_receipt(pending)
        with pytest.raises(
            bot.UnresolvedMemePostReceipt,
            match="pending-schedule",
        ):
            bot.write_meme_post_receipt(full)
        assert bot.load_meme_post_receipt() == ("sending", attempting)


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_current_full_receipt_rejects_future_schedule_version(
    lane: str,
) -> None:
    attempt = schema_current_main_attempt(lane)
    attempt["lifecycle_state"] = "attempting"
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempt,
        post_id="950001" if lane == "quote_image" else "970001",
        confirmation_epoch=1_800_000_100,
    )
    if lane == "quote_image":
        receipt = bot.materialize_bound_regular_schedule_receipt(pending)
        receipt["meme_schedule_version"] = bot.MEME_SCHEDULE_VERSION + 1
        assert bot.regular_post_receipt_is_semantically_valid(receipt) is False
    else:
        receipt = bot.materialize_bound_meme_schedule_receipt(pending)
        receipt["meme_schedule_version"] = bot.MEME_SCHEDULE_VERSION + 1
        assert bot.meme_post_receipt_is_semantically_valid(receipt) is False


def test_bound_quote_anchored_meme_schedule_accepts_cross_midnight_target() -> None:
    anchor_epoch = int(
        datetime(
            2026,
            7,
            6,
            23,
            50,
            0,
            tzinfo=ZoneInfo(bot.MAIN_POST_SCHEDULE_TIMEZONE),
        ).timestamp()
    )
    target_epoch = anchor_epoch + 3600
    assert bot.safe_bound_schedule_date_str(
        target_epoch,
        bot.MAIN_POST_SCHEDULE_TIMEZONE,
    ) != bot.safe_bound_schedule_date_str(
        anchor_epoch,
        bot.MAIN_POST_SCHEDULE_TIMEZONE,
    )
    snapshot = bot.bound_meme_schedule_state(
        {
            "next_meme_post_epoch": target_epoch,
            "meme_schedule_version": 2,
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "next_meme_schedule_date": bot.safe_bound_schedule_date_str(
                anchor_epoch,
                bot.MAIN_POST_SCHEDULE_TIMEZONE,
            ),
            "meme_anchor_quote_post_epoch": anchor_epoch,
        },
        schedule_timezone=bot.MAIN_POST_SCHEDULE_TIMEZONE,
    )

    assert bot.bound_meme_schedule_state_is_valid(snapshot) is True
    quote_hash = bot.quote_text_hash("Good quote.")
    attempt = bot.build_main_post_attempt(
        lane="quote_image",
        text="Good quote.",
        media_ids=["media-1"],
        made_with_ai=False,
        selected_identity={
            "quote_hash": quote_hash,
            "line_no": 0,
            "source_line_number": 1,
            "image_basename": "t01.jpg",
            "image_no": 0,
        },
        recovery_plan={
            "quote_delay_seconds": 7200,
            "meme_delay_seconds": 3600,
            "meme_scheduling_enabled": True,
            "meme_trigger_after_hour": 12,
            "meme_schedule_version": 2,
            "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
            "meme_schedule_before": snapshot,
            "quote_history_after": [quote_hash],
            "image_history_after": ["t01.jpg"],
        },
        attempt_epoch=anchor_epoch,
    )
    assert bot.main_post_attempt_is_semantically_valid(attempt) is True


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_confirmation_requires_consumed_attempt_and_clamps_clock_rollback(
    lane: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sending = schema_current_main_attempt(lane)
    post_id = "950001" if lane == "quote_image" else "970001"
    with pytest.raises(RuntimeError, match="invalid main-post attempt"):
        bot.build_confirmed_pending_schedule_receipt(
            sending,
            post_id=post_id,
            confirmation_epoch=1_800_000_100,
        )

    attempting = {**sending, "lifecycle_state": "attempting"}
    with pytest.raises(RuntimeError, match="invalid main-post attempt"):
        bot.build_confirmed_pending_schedule_receipt(
            attempting,
            post_id=post_id,
            confirmation_epoch=1_799_999_999,
        )
    confirmation_epoch = bot.confirmation_epoch_for_main_attempt(
        attempting,
        1_799_999_999,
    )
    assert confirmation_epoch == 1_800_000_000
    assert "Wall clock moved backwards" in caplog.text
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempting,
        post_id=post_id,
        confirmation_epoch=confirmation_epoch,
    )
    assert pending["confirmation_epoch"] == attempting["attempt_epoch"]
    boolean_schema = copy.deepcopy(pending)
    boolean_schema["schema_version"] = True
    assert (
        bot.confirmed_pending_schedule_receipt_is_semantically_valid(
            boolean_schema,
            expected_lane=lane,
        )
        is False
    )


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_main_attempt_authorises_exactly_one_remote_create(
    lane: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = schema_current_main_attempt(lane)
    bot.write_main_post_attempt(attempt)
    remote_calls = 0

    def create_once(*_args: object, **_kwargs: object) -> dict:
        nonlocal remote_calls
        remote_calls += 1
        return {"data": {"id": "950001" if lane == "quote_image" else "970001"}}

    install_receipt_bound_x_request_stub(monkeypatch, create_once)

    bot.create_post(
        text=str(attempt["text"]),
        media_ids=list(attempt["media_ids"]),
        made_with_ai=bool(attempt["made_with_ai"]),
        prepared_main_post_attempt=attempt,
    )
    assert remote_calls == 1
    assert attempt["lifecycle_state"] == "attempting"

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.create_post(
            text=str(attempt["text"]),
            media_ids=list(attempt["media_ids"]),
            made_with_ai=bool(attempt["made_with_ai"]),
            prepared_main_post_attempt=attempt,
        )
    assert remote_calls == 1

    loader = (
        bot.load_regular_post_receipt
        if lane == "quote_image"
        else bot.load_meme_post_receipt
    )
    assert loader() == ("sending", attempt)


def test_regular_generic_4xx_retains_sending_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    lines_used, images_used, state, *_paths = configure_simple_quote_post(
        tmp_path,
        monkeypatch,
    )

    def definite_failure(
        method: str,
        path: str,
        **_kwargs: object,
    ) -> dict:
        assert method == "POST"
        assert path == "/2/tweets"
        status, attempt = bot.load_regular_post_receipt()
        assert status == "sending"
        assert attempt is not None
        assert attempt["lifecycle_state"] == "attempting"
        raise bot.ApiError(
            "definite rejection",
            service="x",
            status_code=400,
            request_method="POST",
            request_path="/2/tweets",
        )

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(monkeypatch, definite_failure)
    with pytest.raises(bot.ApiError, match="definite rejection"):
        bot.post_random_quote(lines_used, images_used, state)

    status, attempt = bot.load_regular_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lifecycle_state"] == "attempting"
    assert bot.ambiguous_remote_post_is_blocking() is True


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            bot.ApiError(
                "explicit create rejection",
                service="x",
                status_code=400,
                request_method="POST",
                request_path="/2/tweets",
            ),
            False,
        ),
        (
            bot.ApiError(
                "status unavailable",
                service="x",
                request_method="POST",
                request_path="/2/tweets",
            ),
            False,
        ),
        (
            bot.ApiError(
                "server failure",
                service="x",
                status_code=500,
                request_method="POST",
                request_path="/2/tweets",
            ),
            False,
        ),
        (
            bot.ApiError(
                "other service",
                service="xai",
                status_code=400,
                request_method="POST",
                request_path="/2/tweets",
            ),
            False,
        ),
        (
            bot.ApiError(
                "read request",
                service="x",
                status_code=400,
                request_method="GET",
                request_path="/2/tweets",
            ),
            False,
        ),
        (
            bot.ApiError(
                "wrong endpoint",
                service="x",
                status_code=400,
                request_method="POST",
                request_path="/2/media/upload",
            ),
            False,
        ),
        (
            bot.AmbiguousRemotePostOutcome(
                "explicitly uncertain",
                service="x",
                status_code=400,
                request_method="POST",
                request_path="/2/tweets",
            ),
            False,
        ),
        (
            bot.RemoteOperationsPaused("local maintenance pause"),
            True,
        ),
    ],
)
def test_only_local_pause_proves_remote_non_success(
    error: BaseException,
    expected: bool,
) -> None:
    assert bot.api_error_proves_remote_non_success(error) is expected


@pytest.mark.parametrize(
    "failure_kind",
    ["http_500", "status_unknown", "unexpected", "transport"],
)
def test_regular_uncertain_remote_failure_retains_attempt_and_blocks_retry(
    failure_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    actual_x_request = bot.x_request
    lines_used, images_used, state, *_paths = configure_simple_quote_post(
        tmp_path,
        monkeypatch,
    )
    attempts = 0

    def uncertain_request(
        _method: str,
        _path: str,
        **_kwargs: object,
    ) -> dict:
        nonlocal attempts
        attempts += 1
        if failure_kind == "http_500":
            raise bot.ApiError("uncertain 500", service="x", status_code=500)
        if failure_kind == "status_unknown":
            raise bot.ApiError("unknown status", service="x")
        raise RuntimeError("unexpected transport wrapper failure")

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    if failure_kind == "transport":
        monkeypatch.setattr(bot, "x_request", actual_x_request)

        def transport_failure(*_args: object, **_kwargs: object) -> object:
            nonlocal attempts
            attempts += 1
            raise bot.requests.ConnectionError("connection dropped")

        monkeypatch.setattr(bot.requests, "request", transport_failure)
        expected_error: type[BaseException] = bot.AmbiguousRemotePostOutcome
    else:
        install_receipt_bound_x_request_stub(monkeypatch, uncertain_request)
        expected_error = (
            RuntimeError
            if failure_kind == "unexpected"
            else bot.ApiError
        )

    with pytest.raises(expected_error):
        bot.post_random_quote(lines_used, images_used, state)

    status, attempt = bot.load_regular_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lifecycle_state"] == "attempting"
    assert attempts == 1

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_random_quote(set(), set(), {})
    assert attempts == 1


def test_regular_normal_success_atomically_promotes_sending_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    (
        lines_used,
        images_used,
        state,
        _lines_used_file,
        _images_used_file,
        receipt_file,
        _lines_file,
    ) = configure_simple_quote_post(tmp_path, monkeypatch)
    observed_attempt: dict = {}

    def confirmed_create(
        method: str,
        path: str,
        **_kwargs: object,
    ) -> dict:
        assert method == "POST"
        assert path == "/2/tweets"
        status, attempt = bot.load_regular_post_receipt()
        assert status == "sending"
        assert attempt is not None
        assert attempt["lifecycle_state"] == "attempting"
        observed_attempt.update(attempt)
        return {"data": {"id": "950001"}}

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(monkeypatch, confirmed_create)
    monkeypatch.setattr(
        bot, "remove_regular_post_receipt", lambda *_args, **_kwargs: None
    )
    bot.post_random_quote(lines_used, images_used, state)

    status, confirmed = bot.load_regular_post_receipt()
    assert status == "valid"
    assert confirmed is not None
    assert confirmed["post_id"] == "950001"
    assert confirmed["attempt_id"] == observed_attempt["attempt_id"]
    assert (
        confirmed["attempt_payload_sha256"]
        == observed_attempt["payload_sha256"]
    )
    assert receipt_file.exists()


@pytest.mark.parametrize(
    ("context_required", "expected_context_state"),
    [
        (True, "context_reply_pending"),
        (False, "context_reply_not_required"),
    ],
)
def test_regular_promotion_failure_records_context_before_attempt_retirement(
    context_required: bool,
    expected_context_state: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    lines_used, images_used, state, *_paths = configure_simple_quote_post(
        tmp_path,
        monkeypatch,
    )
    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *_args, **_kwargs: {"data": {"id": "950001"}},
    )
    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("confirmed promotion failed")
        ),
    )
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {
            **bot.historical_context_reply,
            "enabled": context_required,
        },
    )
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON",
        None,
    )

    with pytest.raises(
        bot.ConfirmedPostLocalPersistenceError,
        match="failed local recovery receipt",
    ):
        bot.post_random_quote(lines_used, images_used, state)

    assert bot.load_regular_post_receipt() == ("absent", None)
    obligation = bot.historical_context_outbox_store().get("950001")
    assert obligation is not None
    assert obligation["main_post"]["state"] == "main_post_confirmed"
    assert obligation["context_reply"]["state"] == expected_context_state


def test_regular_promotion_and_required_context_enqueue_failure_retains_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    lines_used, images_used, state, *_paths = configure_simple_quote_post(
        tmp_path,
        monkeypatch,
    )
    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *_args, **_kwargs: {"data": {"id": "950001"}},
    )
    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("confirmed promotion failed")
        ),
    )
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON",
        None,
    )
    monkeypatch.setattr(
        bot,
        "enqueue_historical_context_obligation",
        lambda _receipt: (_ for _ in ()).throw(
            OSError("required context enqueue failed")
        ),
    )

    with pytest.raises(
        bot.ConfirmedPostLocalPersistenceError,
        match="historical-context disposition",
    ):
        bot.post_random_quote(lines_used, images_used, state)

    status, attempt = bot.load_regular_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lifecycle_state"] == "attempting"
    assert attempt["selected_identity"]["quote_hash"] == bot.quote_text_hash(
        "Good quote."
    )
    assert bot.ambiguous_remote_post_is_blocking() is True


def test_regular_ambiguous_create_without_marker_uses_durable_attempt_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_atomic_write = bot.atomic_write_json
    lines_used, images_used, state, *_paths = configure_simple_quote_post(
        tmp_path,
        monkeypatch,
    )

    def ambiguous_create(**kwargs: object) -> dict:
        mock_confirmed_main_post(kwargs, {})
        bot.record_ambiguous_remote_post({"text": "Good quote."})
        raise bot.AmbiguousRemotePostOutcome("ambiguous", service="x")

    monkeypatch.setattr(bot, "create_post", ambiguous_create)
    def fail_only_ambiguous_marker(
        path: Path,
        value: object,
        **kwargs: object,
    ) -> None:
        if Path(path) == bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE:
            raise OSError("marker failed")
        original_atomic_write(path, value, **kwargs)

    monkeypatch.setattr(bot, "atomic_write_json", fail_only_ambiguous_marker)
    guard_token = object()
    ended_guards: list[object | None] = []
    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", lambda: guard_token)
    monkeypatch.setattr(
        bot,
        "end_confirmed_post_sigint_deferral",
        lambda guard: ended_guards.append(guard),
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_random_quote(lines_used, images_used, state)

    assert bot.ambiguous_remote_post_is_blocking() is True
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert ended_guards == [guard_token]


def test_confirmed_post_sigint_deferral_restores_handler_and_delivers_pending() -> None:
    prior_handler = signal.getsignal(signal.SIGINT)
    guard = bot.begin_confirmed_post_sigint_deferral()
    try:
        guard.handle(signal.SIGINT, None)
        assert guard.pending is True
        with pytest.raises(KeyboardInterrupt):
            bot.end_confirmed_post_sigint_deferral(guard)
    finally:
        signal.signal(signal.SIGINT, prior_handler)

    assert signal.getsignal(signal.SIGINT) == prior_handler


def test_confirmed_post_sigint_deferral_handles_process_signal_from_worker() -> None:
    code = """
import os
import signal
import threading
import time
import mrsMThatcher2 as bot

guard = bot.begin_confirmed_post_sigint_deferral()
sender = threading.Thread(target=lambda: os.kill(os.getpid(), signal.SIGINT))
sender.start()
sender.join()
deadline = time.monotonic() + 2
while not guard.pending and time.monotonic() < deadline:
    time.sleep(0.01)
if not guard.pending:
    raise SystemExit("SIGINT was not deferred")
try:
    bot.end_confirmed_post_sigint_deferral(guard)
except KeyboardInterrupt:
    print("deferred-and-delivered")
else:
    raise SystemExit("pending SIGINT was not delivered")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(bot.__file__).resolve().parent,
        env={**os.environ, **IMPORT_ENV},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("deferred-and-delivered")


def test_regular_no_receipt_emergency_representation_reloads_completely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        lines_used,
        images_used,
        state,
        _lines_used_file,
        _images_used_file,
        receipt_file,
        lines_file,
    ) = configure_simple_quote_post(tmp_path, monkeypatch)
    pending_receipts: list[dict] = []

    def fail_receipt(attempt: dict, **kwargs: object) -> None:
        pending_receipts.append(
            bot.build_confirmed_pending_schedule_receipt(attempt, **kwargs)
        )
        raise OSError("receipt failed")

    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        fail_receipt,
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    assert not receipt_file.exists()
    reloaded_state = bot.load_runtime_state()
    reloaded_lines = bot.load_quote_used_hashes(
        lines_file.read_text(encoding="utf-8").splitlines(keepends=True)
    )
    image_paths = bot.current_image_paths()
    reloaded_images = bot.load_image_used_basenames(image_paths)
    quote_hash = bot.quote_text_hash("Good quote.")

    assert bot.confirmed_regular_emergency_representation_is_complete(
        post_id="950001",
        post_epoch=1_800_000_000,
        quote_hash=quote_hash,
        image_basename="t01.jpg",
        lines_used=reloaded_lines,
        images_used=reloaded_images,
        state=reloaded_state,
        main_post_attempt=pending_receipts[0]["source_attempt"],
    )
    assert reloaded_state["next_quote_post_epoch"] > reloaded_state["last_quote_post_epoch"]


def test_regular_emergency_canonical_state_survives_backup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, *_paths = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt failed")),
    )
    monkeypatch.setattr(
        bot,
        "write_latest_state_backup",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("backup failed")),
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError) as caught:
        bot.post_random_quote(lines_used, images_used, state)

    assert type(caught.value) is bot.ConfirmedPostLocalPersistenceError
    assert bot.json_file_matches(bot.STATE_FILE, state) is True
    assert bot.ambiguous_remote_post_is_blocking() is False


def test_regular_emergency_parent_fsync_failure_still_latches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, *_paths = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt failed")),
    )
    original_fsync_parent_dir = bot.fsync_parent_dir

    def fail_state_parent_fsync(path: Path, *, strict: bool = False) -> None:
        if path == bot.STATE_FILE:
            raise OSError("state parent fsync failed")
        original_fsync_parent_dir(path, strict=strict)

    monkeypatch.setattr(bot, "fsync_parent_dir", fail_state_parent_fsync)

    with pytest.raises(bot.UnrecoverableConfirmedPostPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    assert bot.json_file_matches(bot.STATE_FILE, state) is True
    assert bot.ambiguous_remote_post_is_blocking() is True
    marker = json.loads(bot.AMBIGUOUS_POST_OUTCOME_FILE.read_text(encoding="utf-8"))
    assert "state" in marker["failure_components"]


@pytest.mark.parametrize(
    "failed_components",
    [
        {"quote_history"},
        {"image_history"},
        {"state"},
        {"quote_history", "image_history"},
        {"quote_history", "state"},
        {"image_history", "state"},
        {"quote_history", "image_history", "state"},
    ],
)
def test_receipt_write_failure_emergency_persistence_attempts_all_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_components: set[str],
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    attempts: list[str] = []
    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt failed")),
    )

    def maybe_fail(name: str) -> None:
        attempts.append(name)
        if name in failed_components:
            raise OSError(f"{name} failed")

    monkeypatch.setattr(bot, "save_quote_used_hashes", lambda *args, **kwargs: maybe_fail("quote_history"))
    monkeypatch.setattr(bot, "save_image_used_basenames", lambda *args, **kwargs: maybe_fail("image_history"))
    monkeypatch.setattr(bot, "save_state", lambda *args, **kwargs: maybe_fail("state"))

    with pytest.raises(bot.UnrecoverableConfirmedPostPersistenceError) as excinfo:
        bot.post_random_quote(lines_used, images_used, state)

    assert attempts == ["quote_history", "image_history", "state"]
    assert bot.ambiguous_remote_post_is_blocking() is True
    marker = json.loads(bot.AMBIGUOUS_POST_OUTCOME_FILE.read_text(encoding="utf-8"))
    assert marker["outcome"] == "confirmed_remote_post_local_persistence_failed"
    assert marker["lane"] == "quote_image"
    assert marker["post_id"] == "950001"
    assert marker["failure_components"] == sorted({"regular_post_receipt", *failed_components})
    for name in failed_components:
        assert name in str(excinfo.value)
    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]


def test_regular_total_persistence_loss_latches_when_marker_write_also_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_atomic_write = bot.atomic_write_json
    original_create_post = bot.create_post
    lines_used, images_used, state, *_paths = configure_simple_quote_post(tmp_path, monkeypatch)
    remote_calls = 0

    def confirmed_create(**kwargs: object) -> dict:
        nonlocal remote_calls
        remote_calls += 1
        return mock_confirmed_main_post(
            kwargs,
            {"data": {"id": "950001"}},
        )

    monkeypatch.setattr(bot, "create_post", confirmed_create)
    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt failed")),
    )
    monkeypatch.setattr(
        bot,
        "save_quote_used_hashes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("quote history failed")),
    )
    monkeypatch.setattr(
        bot,
        "save_image_used_basenames",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("image history failed")),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("state failed")),
    )
    def fail_only_ambiguous_marker(
        path: Path,
        value: object,
        **kwargs: object,
    ) -> None:
        if Path(path) == bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE:
            raise OSError("marker failed")
        original_atomic_write(path, value, **kwargs)

    monkeypatch.setattr(bot, "atomic_write_json", fail_only_ambiguous_marker)
    guard_token = object()
    ended_guards: list[object | None] = []
    monkeypatch.setattr(
        bot,
        "begin_confirmed_post_sigint_deferral",
        lambda: guard_token,
    )
    monkeypatch.setattr(
        bot,
        "end_confirmed_post_sigint_deferral",
        lambda guard: ended_guards.append(guard),
    )

    with pytest.raises(bot.UnrecoverableConfirmedPostPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    assert remote_calls == 1
    assert bot.ambiguous_remote_post_is_blocking() is True
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert ended_guards == [guard_token]
    boundary_calls = 0

    def unexpected_boundary(*_args: object, **_kwargs: object) -> dict:
        nonlocal boundary_calls
        boundary_calls += 1
        pytest.fail("remote boundary must not be reached after the safety latch")

    install_receipt_bound_x_request_stub(monkeypatch, unexpected_boundary)
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="in-process remote-write safety latch"):
        original_create_post("must not be sent")
    assert remote_calls == 1
    assert boundary_calls == 0


def test_confirmation_clock_failure_uses_durable_attempt_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, *_paths = configure_simple_quote_post(tmp_path, monkeypatch)
    state.update(
        {
            "last_main_post_id": "940000",
            "last_quote_post_epoch": 1_700_000_000,
            "next_quote_post_epoch": 1_700_007_200,
        }
    )
    clock_calls = 0

    def fail_after_preflight() -> int:
        nonlocal clock_calls
        clock_calls += 1
        if clock_calls == 1:
            return 1_800_000_000
        raise RuntimeError("clock failed after confirmation")

    monkeypatch.setattr(
        bot,
        "now_epoch",
        fail_after_preflight,
    )
    monkeypatch.setattr(
        bot,
        "safely_process_due_historical_context_obligations",
        lambda **_kwargs: [],
    )

    bot.post_random_quote(lines_used, images_used, state)

    assert clock_calls == 2
    assert bot.ambiguous_remote_post_is_blocking() is False
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert state["last_quote_post_epoch"] == 1_800_000_000
    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
    assert not bot.inspect_transport_state(
        bot.journal_path_for_receipt(bot.REGULAR_POST_RECEIPT_FILE)
    ).blocking


@pytest.mark.parametrize("observed", [1_499_999_999, 4_102_444_801])
def test_out_of_range_confirmation_clock_uses_durable_attempt_epoch(
    monkeypatch: pytest.MonkeyPatch,
    observed: int,
) -> None:
    source = schema_current_main_attempt("quote_image")
    fallback = int(source["attempt_epoch"])
    monkeypatch.setattr(bot, "now_epoch", lambda: observed)

    assert bot.confirmation_epoch_after_remote_success(source) == fallback


def test_confirmation_clock_requires_valid_durable_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)

    with pytest.raises(
        bot.TransportJournalError,
        match="no durable confirmation-time fallback",
    ):
        bot.confirmation_epoch_after_remote_success(
            {"attempt_epoch": 4_102_444_801}
        )


def test_confirmed_regular_post_fallback_helper_failure_latches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, *_paths = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt failed")),
    )
    monkeypatch.setattr(
        bot,
        "update_regular_generated_image_spacing_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("spacing failed")),
    )

    with pytest.raises(bot.UnrecoverableConfirmedPostPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    marker = json.loads(bot.AMBIGUOUS_POST_OUTCOME_FILE.read_text(encoding="utf-8"))
    assert "generated_image_spacing_state" in marker["failure_components"]
    assert bot.ambiguous_remote_post_is_blocking() is True


@pytest.mark.parametrize("helper", ["cache_tweet", "record_recent_own_post"])
def test_regular_post_helper_failure_after_confirmation_leaves_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    helper: str,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(bot, helper, lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(f"{helper} failed")))

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    assert receipt_file.exists()
    assert json.loads(receipt_file.read_text(encoding="utf-8"))["post_id"] == "950001"
    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]


def test_regular_post_does_not_call_mutating_schedule_helpers_after_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(bot, "schedule_next_quote_post", lambda *args, **kwargs: pytest.fail("schedule_next_quote_post should not be called"))
    monkeypatch.setattr(bot, "maybe_schedule_meme_after_quote_post", lambda *args, **kwargs: pytest.fail("maybe_schedule_meme_after_quote_post should not be called"))

    bot.post_random_quote(lines_used, images_used, state)

    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]


def test_existing_valid_receipt_is_reconciled_before_next_regular_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, lines_used_file, _images_used_file, receipt_file, lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    old_hash = bot.quote_text_hash("Old quote.")
    receipt = {
        "schema_version": 1,
        "post_id": "940001",
        "quote_hash": old_hash,
        "line_no": 0,
        "source_line_number": 1,
        "text": "Old quote.",
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
    }
    bot.atomic_write_json(receipt_file, receipt)
    lines_file.write_text("Old quote.\nGood quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(["Old quote.", "Good quote."]))

    bot.post_random_quote(lines_used, images_used, state)

    assert not receipt_file.exists()
    assert old_hash in json.loads(lines_used_file.read_text(encoding="utf-8"))
    assert receipt_file.read_text(encoding="utf-8") if receipt_file.exists() else True


def test_receipt_is_not_removed_when_protected_durable_save_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt = {
        "schema_version": 1,
        "post_id": "950001",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    bot.atomic_write_json(receipt_file, receipt)
    monkeypatch.setattr(bot, "save_image_used_basenames", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("image save failed")))

    with pytest.raises(OSError):
        bot.reconcile_regular_post_receipt(lines_used, images_used, state)

    assert receipt_file.exists()


def test_protected_durable_saves_complete_before_receipt_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    calls: list[str] = []
    receipt = {
        "schema_version": 1,
        "post_id": "950001",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    bot.atomic_write_json(receipt_file, receipt)
    monkeypatch.setattr(bot, "save_quote_used_hashes", lambda *args, **kwargs: calls.append("quote"))
    monkeypatch.setattr(bot, "save_image_used_basenames", lambda *args, **kwargs: calls.append("image"))
    monkeypatch.setattr(bot, "save_state", lambda *args, **kwargs: calls.append("state"))
    monkeypatch.setattr(
        bot,
        "remove_regular_post_receipt",
        lambda *_args, **_kwargs: calls.append("remove"),
    )

    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is True
    assert calls == ["quote", "image", "state", "remove"]


def test_malformed_receipt_blocks_new_regular_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt_file.write_text("{bad json", encoding="utf-8")

    with pytest.raises(bot.InvalidRegularPostReceipt):
        bot.post_random_quote(lines_used, images_used, state)

    assert receipt_file.exists()
    assert lines_used == set()
    assert images_used == set()


@pytest.mark.parametrize(
    "patch",
    [
        {"post_id": ""},
        {"post_id": "not-a-number"},
        {"quote_hash": "abc"},
        {"quote_post_epoch": "soon"},
        {"quote_post_epoch": 1},
        {"next_quote_post_epoch": "soon"},
        {"next_quote_post_epoch": 1_800_000_000},
        {"next_quote_post_epoch": 1_799_999_999},
        {"text": ""},
        {"text": None},
        {"image_basename": "../t01.jpg"},
        {"text": "Different text"},
        {"next_meme_post_epoch": 1_799_999_999, "next_meme_schedule_date": "2027-01-15", "next_meme_schedule_mode": "fallback"},
        {"next_meme_post_epoch": 1_800_086_400, "next_meme_schedule_date": "2000-01-01", "next_meme_schedule_mode": "fallback"},
        {"next_meme_post_epoch": 1_800_086_400, "next_meme_schedule_date": "2027-01-16", "next_meme_schedule_mode": "surprise"},
        {
            "next_meme_post_epoch": 1_800_000_600,
            "next_meme_schedule_date": "2027-01-15",
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "meme_anchor_quote_post_epoch": 1,
        },
        {
            "next_meme_post_epoch": 1_800_000_600,
            "next_meme_schedule_date": "2027-01-15",
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "meme_anchor_quote_post_epoch": 0,
            "meme_schedule_changed_by_quote": False,
        },
        {
            "next_meme_post_epoch": 1_800_000_000,
            "next_meme_schedule_date": bot.epoch_date_str(1_800_000_000),
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "meme_anchor_quote_post_epoch": 1_800_000_000,
            "meme_schedule_changed_by_quote": False,
        },
    ],
)
def test_semantically_invalid_receipts_block_main_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patch: dict,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt = {
        "schema_version": 1,
        "post_id": "950001",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    receipt.update(patch)
    bot.atomic_write_json(receipt_file, receipt)

    with pytest.raises(bot.InvalidRegularPostReceipt):
        bot.post_random_quote(lines_used, images_used, state)


def test_regular_receipt_accepts_all_runtime_meme_schedule_modes() -> None:
    next_meme_epoch = 1_800_086_400
    for mode in sorted(bot.MEME_SCHEDULE_MODES - {"", "after_first_quote_after_midday"}):
        receipt = valid_regular_receipt(
            next_meme_post_epoch=next_meme_epoch,
            next_meme_schedule_mode=mode,
            next_meme_schedule_date=bot.epoch_date_str(next_meme_epoch),
            meme_anchor_quote_post_epoch=0,
            meme_schedule_changed_by_quote=False,
        )
        assert bot.regular_post_receipt_is_semantically_valid(receipt), mode


def test_receipt_validators_reject_boolean_schema_versions_and_fractional_epochs() -> None:
    regular = valid_regular_receipt()
    assert bot.regular_post_receipt_is_semantically_valid({**regular, "schema_version": True}) is False
    assert bot.regular_post_receipt_is_semantically_valid({**regular, "quote_post_epoch": 1_800_000_000.5}) is False

    meme = {
        "schema_version": 1,
        "post_id": "950001",
        "meme_basename": "meme.png",
        "meme_post_epoch": 1_800_000_000,
        "next_meme_post_epoch": 1_800_086_400,
        "next_meme_schedule_mode": "fallback",
    }
    assert bot.meme_post_receipt_is_semantically_valid({**meme, "schema_version": True}) is False
    assert bot.meme_post_receipt_is_semantically_valid({**meme, "meme_post_epoch": 1_800_000_000.5}) is False

    reply = {
        "schema_version": 1,
        "target_id": "100",
        "reply_post_id": "900000",
        "author_id": "200",
        "reply_epoch": 1_800_000_000,
        "candidate_source": "mention",
        "reply_text": "A reply.",
    }
    assert bot.confirmed_reply_receipt_is_semantically_valid({**reply, "schema_version": True}) is False
    assert bot.confirmed_reply_receipt_is_semantically_valid({**reply, "reply_epoch": 1_800_000_000.5}) is False


def test_regular_receipt_distinguishes_quote_created_and_preserved_meme_schedules() -> None:
    quote_epoch = 1_800_000_000
    quote_created = valid_regular_receipt(
        quote_post_epoch=quote_epoch,
        next_quote_post_epoch=quote_epoch + 7200,
        next_meme_post_epoch=quote_epoch + 3600,
        next_meme_schedule_mode="after_first_quote_after_midday",
        next_meme_schedule_date=bot.epoch_date_str(quote_epoch),
        meme_anchor_quote_post_epoch=quote_epoch,
        meme_schedule_changed_by_quote=True,
    )
    assert bot.regular_post_receipt_is_semantically_valid(quote_created)

    anchor_epoch = quote_epoch - 7200
    preserved_overdue = valid_regular_receipt(
        quote_post_epoch=quote_epoch,
        next_quote_post_epoch=quote_epoch + 7200,
        next_meme_post_epoch=quote_epoch - 1800,
        next_meme_schedule_mode="after_first_quote_after_midday",
        next_meme_schedule_date=bot.epoch_date_str(anchor_epoch),
        meme_anchor_quote_post_epoch=anchor_epoch,
        meme_schedule_changed_by_quote=False,
    )
    assert bot.regular_post_receipt_is_semantically_valid(preserved_overdue)


def test_cross_midnight_first_quote_meme_anchor_is_not_rescheduled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "MEME_TRIGGER_AFTER_HOUR", 12)
    quote_one = int(datetime(2026, 7, 6, 23, 30).timestamp())
    quote_two = int(datetime(2026, 7, 6, 23, 50).timestamp())
    state = {"next_meme_post_epoch": 0, "last_meme_post_epoch": 0}

    fields = bot.meme_schedule_fields_after_quote_post(state, quote_one, delay=3600)
    assert fields["next_meme_schedule_mode"] == "after_first_quote_after_midday"
    assert fields["next_meme_schedule_date"] == "2026-07-06"
    assert bot.epoch_date_str(fields["next_meme_post_epoch"]) == "2026-07-07"
    state.update(fields)

    assert bot.meme_schedule_fields_after_quote_post(state, quote_two, delay=1800) == {}
    assert state["meme_anchor_quote_post_epoch"] == quote_one
    assert state["next_meme_post_epoch"] == quote_one + 3600


def test_regular_receipt_validates_cross_midnight_quote_anchored_meme_schedule() -> None:
    quote_epoch = int(datetime(2026, 7, 6, 23, 30).timestamp())
    receipt = valid_regular_receipt(
        quote_post_epoch=quote_epoch,
        next_quote_post_epoch=quote_epoch + 7200,
        next_meme_post_epoch=quote_epoch + 3600,
        next_meme_schedule_mode="after_first_quote_after_midday",
        next_meme_schedule_date="2026-07-06",
        meme_anchor_quote_post_epoch=quote_epoch,
        meme_schedule_changed_by_quote=True,
    )
    assert bot.regular_post_receipt_is_semantically_valid(receipt)


def test_delayed_meme_schedule_modes_clear_stale_quote_anchor() -> None:
    delayed_modes = {
        "delayed_runtime_control",
        "delayed_recent_quote",
        "delayed_write_api_cooldown",
        "delayed_api_error",
        "delayed_exception",
    }
    for mode in delayed_modes:
        state = {
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "meme_anchor_quote_post_epoch": 1_800_000_000,
        }
        target_epoch = 1_800_010_000
        bot.set_meme_delay_schedule(state, epoch=target_epoch, mode=mode, save=False)
        assert state["next_meme_post_epoch"] == target_epoch
        assert state["next_meme_schedule_mode"] == mode
        assert state["next_meme_schedule_date"] == bot.epoch_date_str(target_epoch)
        assert state["meme_anchor_quote_post_epoch"] == 0


def test_all_meme_schedule_modes_preserve_anchor_invariants() -> None:
    target_epoch = 1_800_010_000
    anchor_epoch = int(datetime(2026, 7, 6, 13, 0).timestamp())
    fallback_modes = bot.MEME_SCHEDULE_MODES - {
        "",
        "after_first_quote_after_midday",
        "delayed_runtime_control",
        "delayed_recent_quote",
        "delayed_write_api_cooldown",
        "delayed_api_error",
        "delayed_exception",
    }
    delayed_modes = {
        "delayed_runtime_control",
        "delayed_recent_quote",
        "delayed_write_api_cooldown",
        "delayed_api_error",
        "delayed_exception",
    }

    for mode in fallback_modes:
        fields = bot.next_meme_schedule_fields({}, target_epoch - 1, mode=mode)
        assert fields["next_meme_schedule_mode"] == mode
        assert fields["meme_anchor_quote_post_epoch"] == 0
        assert fields["next_meme_schedule_date"] == bot.epoch_date_str(fields["next_meme_post_epoch"])

    for mode in delayed_modes:
        fields = bot.meme_delay_schedule_fields(target_epoch, mode)
        assert fields["next_meme_schedule_mode"] == mode
        assert fields["meme_anchor_quote_post_epoch"] == 0
        assert fields["next_meme_schedule_date"] == bot.epoch_date_str(target_epoch)

    quote_fields = bot.meme_schedule_fields_after_quote_post({"last_meme_post_epoch": 0}, anchor_epoch, delay=3600)
    assert quote_fields["next_meme_schedule_mode"] == "after_first_quote_after_midday"
    assert quote_fields["meme_anchor_quote_post_epoch"] == anchor_epoch
    assert quote_fields["next_meme_schedule_date"] == bot.epoch_date_str(anchor_epoch)


def test_delayed_schedule_followed_by_regular_quote_produces_self_validating_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.set_meme_delay_schedule(state, epoch=1_800_010_000, mode="delayed_recent_quote", save=False)
    receipts: list[dict] = []
    original_write = bot.write_regular_post_receipt

    def capture_receipt(receipt: dict) -> None:
        if bot.confirmed_pending_schedule_receipt_is_semantically_valid(
            receipt,
            expected_lane="quote_image",
        ):
            original_write(receipt)
            return
        assert bot.regular_post_receipt_is_semantically_valid(receipt)
        receipts.append(json.loads(json.dumps(receipt)))
        original_write(receipt)

    monkeypatch.setattr(bot, "write_regular_post_receipt", capture_receipt)

    bot.post_random_quote(lines_used, images_used, state)

    assert receipts
    assert receipts[0]["schema_version"] == 3
    assert receipts[0]["quote_history_after"] == [
        bot.quote_text_hash("Good quote.")
    ]
    assert receipts[0]["image_history_after"] == ["t01.jpg"]
    assert receipts[0]["next_meme_schedule_mode"] == "delayed_recent_quote"
    assert receipts[0]["meme_schedule_version"] == bot.MEME_SCHEDULE_VERSION
    assert receipts[0]["meme_anchor_quote_post_epoch"] == 0
    assert receipts[0]["meme_schedule_changed_by_quote"] is False


@pytest.mark.parametrize(
    "patch",
    [
        {"next_meme_post_epoch": "banana"},
        {
            "next_meme_post_epoch": 1_800_086_400,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": bot.epoch_date_str(1_800_086_400),
            "meme_anchor_quote_post_epoch": "banana",
            "meme_schedule_changed_by_quote": False,
        },
        {
            "next_meme_post_epoch": 1_800_086_400,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": bot.epoch_date_str(1_800_086_400),
            "meme_anchor_quote_post_epoch": 0,
            "meme_schedule_changed_by_quote": "false",
        },
    ],
)
def test_regular_receipt_malformed_optional_fields_are_invalid_not_exceptions(patch: dict) -> None:
    receipt = valid_regular_receipt(**patch)
    assert bot.regular_post_receipt_is_semantically_valid(receipt) is False


def test_valid_receipt_reconciles_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt = {
        "schema_version": 1,
        "post_id": "950001",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    bot.atomic_write_json(receipt_file, receipt)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    context_calls = []
    def context(**kwargs):
        assert not receipt_file.exists()
        assert bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.exists()
        context_calls.append(kwargs)
        return {"status": "completed", "reply_post_id": "960001"}
    monkeypatch.setattr(bot, "maybe_post_historical_context_reply", context)

    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is True
    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is False
    assert len(context_calls) == 1
    assert lines_used == {bot.quote_text_hash("Good quote.")}
    assert images_used == {"t01.jpg"}


def test_old_receipt_does_not_move_main_post_state_behind_newer_meme(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _lines_used, _images_used, _state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt = {
        "schema_version": 1,
        "post_id": "940002",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    bot.atomic_write_json(receipt_file, receipt)
    state = {
        "last_main_post_id": "960001",
        "last_meme_post_epoch": 1_800_000_200,
        "next_meme_post_epoch": 1_800_100_000,
        "next_meme_schedule_mode": "fallback_future",
        "next_meme_schedule_date": "2027-01-18",
    }
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)

    assert bot.reconcile_regular_post_receipt(set(), set(), state) is True
    assert state["last_main_post_id"] == "960001"
    assert state["next_meme_post_epoch"] == 1_800_100_000
    assert state["next_meme_schedule_mode"] == "fallback_future"


def test_post_next_meme_blocked_by_unresolved_regular_post_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _lines_used, _images_used, _state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.atomic_write_json(
        receipt_file,
        {
            "schema_version": 1,
            "post_id": "950001",
            "quote_hash": bot.quote_text_hash("Good quote."),
            "image_basename": "t01.jpg",
            "quote_post_epoch": 1_800_000_000,
            "next_quote_post_epoch": 1_800_007_200,
            "text": "Good quote.",
        },
    )

    with pytest.raises(bot.UnresolvedRegularPostReceipt):
        bot.post_next_meme({})


def test_regular_receipt_durable_write_uses_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    receipt_file = tmp_path / "regular_post_receipt.json"
    calls: list[str] = []
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot.os, "fsync", lambda fd: calls.append("fsync"))

    bot.write_regular_post_receipt(
        valid_regular_receipt()
    )

    assert receipt_file.exists()
    assert len(calls) >= 2


def test_receipt_writers_self_validate_before_durable_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    regular_receipt_file = tmp_path / "regular_post_receipt.json"
    meme_receipt_file = tmp_path / "meme_post_receipt.json"
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", regular_receipt_file)
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", meme_receipt_file)

    with pytest.raises(RuntimeError, match="semantic validation"):
        bot.write_regular_post_receipt(valid_regular_receipt(next_meme_post_epoch="banana"))
    assert not regular_receipt_file.exists()

    valid_regular_forms = [
        valid_regular_receipt(),
        valid_regular_receipt_v2(),
        valid_regular_receipt(
            next_meme_post_epoch=1_800_086_400,
            next_meme_schedule_mode="fallback",
            next_meme_schedule_date=bot.epoch_date_str(1_800_086_400),
            meme_anchor_quote_post_epoch=0,
            meme_schedule_changed_by_quote=False,
        ),
        valid_regular_receipt(
            next_meme_post_epoch=1_800_003_600,
            next_meme_schedule_mode="after_first_quote_after_midday",
            next_meme_schedule_date=bot.epoch_date_str(1_800_000_000),
            meme_anchor_quote_post_epoch=1_800_000_000,
            meme_schedule_changed_by_quote=True,
        ),
    ]
    for receipt in valid_regular_forms:
        assert bot.regular_post_receipt_is_semantically_valid(receipt)
        bot.write_regular_post_receipt(receipt)
        assert bot.load_regular_post_receipt()[0] == "valid"
        regular_receipt_file.unlink()

    for bad_schema in [{}, {"schema_version": 3}, {"schema_version": "1"}]:
        receipt = valid_regular_receipt()
        receipt.update(bad_schema)
        if "schema_version" not in bad_schema:
            receipt.pop("schema_version", None)
        assert not bot.regular_post_receipt_is_semantically_valid(receipt)
        with pytest.raises(RuntimeError, match="semantic validation"):
            bot.write_regular_post_receipt(receipt)
        assert not regular_receipt_file.exists()

    with pytest.raises(RuntimeError, match="semantic validation"):
        bot.write_meme_post_receipt(
            {
                "schema_version": 1,
                "post_id": "970001",
                "meme_basename": "001_meme.png",
                "meme_post_epoch": 1_800_000_000,
                "next_meme_post_epoch": "banana",
            }
    )
    assert not meme_receipt_file.exists()

    valid_meme_receipt = {
        "schema_version": 1,
        "post_id": "970001",
        "meme_basename": "001_meme.png",
        "meme_post_epoch": 1_800_000_000,
        "next_meme_post_epoch": 1_800_086_400,
        "next_meme_schedule_mode": "fallback",
    }
    assert bot.meme_post_receipt_is_semantically_valid(valid_meme_receipt)
    bot.write_meme_post_receipt(valid_meme_receipt)
    assert bot.load_meme_post_receipt()[0] == "valid"
    meme_receipt_file.unlink()

    for bad_schema in [{}, {"schema_version": 2}, {"schema_version": "1"}]:
        receipt = dict(valid_meme_receipt)
        receipt.update(bad_schema)
        if "schema_version" not in bad_schema:
            receipt.pop("schema_version", None)
        assert not bot.meme_post_receipt_is_semantically_valid(receipt)
        with pytest.raises(RuntimeError, match="semantic validation"):
            bot.write_meme_post_receipt(receipt)
        assert not meme_receipt_file.exists()


def test_load_image_used_basenames_empty_scan_preserves_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    history = tmp_path / "images_used.json"
    history.write_text(json.dumps(["t01.jpg"]) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", history)
    before = history.read_text(encoding="utf-8")

    assert bot.load_image_used_basenames([]) == {"t01.jpg"}
    assert history.read_text(encoding="utf-8") == before


def test_image_history_preserves_missing_basenames_and_reuses_when_file_reappears(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    t01 = image_dir / "t01.jpg"
    t01.write_bytes(b"one")
    history = tmp_path / "images_used.json"
    history.write_text(json.dumps(["t01.jpg", "temporarily_missing.jpg"]) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", history)

    used = bot.load_image_used_basenames([str(t01)])
    assert used == {"t01.jpg", "temporarily_missing.jpg"}
    missing = image_dir / "temporarily_missing.jpg"
    missing.write_bytes(b"two")
    used = bot.load_image_used_basenames([str(t01), str(missing)])
    assert "temporarily_missing.jpg" in used


def test_partial_visible_image_scan_does_not_migrate_legacy_indices_or_rewrite_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    visible = []
    for name in ["t40.jpg", "t41.jpg", "t42.jpg"]:
        path = image_dir / name
        path.write_bytes(name.encode("utf-8"))
        visible.append(path)
    expected = visible + [image_dir / "t43.jpg"]
    expected[-1].write_bytes(b"hidden")
    analysis = image_analysis_for_paths(expected)
    expected[-1].unlink()
    history = tmp_path / "images_used.json"
    history.write_text("[0, 1, 2]\n", encoding="utf-8")
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", history)
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)
    before = history.read_text(encoding="utf-8")

    used = bot.load_image_used_basenames([str(path) for path in visible])

    assert used == {0, 1, 2}
    assert history.read_text(encoding="utf-8") == before


def test_regular_posting_blocks_unsafe_legacy_image_index_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    visible = image_dir / "t40.jpg"
    hidden = image_dir / "t41.jpg"
    visible.write_bytes(b"visible")
    hidden.write_bytes(b"hidden")
    analysis = image_analysis_for_paths([visible, hidden])
    hidden.unlink()
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)
    used = {0}

    with pytest.raises(bot.UnsafeImageHistoryMigration):
        bot.choose_matched_unused_image(used, {"analysis": {"primary_topics": []}}, {})

    assert used == {0}


def test_unsafe_first_quote_index_migration_is_preserved_and_blocks_posting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = ["First quote.", "Second quote."]
    reordered = ["Inserted quote.", "First quote.", "Second quote."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(reordered) + "\n", encoding="utf-8")
    history = tmp_path / "lines_used.json"
    history.write_text("[1]\n", encoding="utf-8")
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "LINES_USED_FILE", history)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(original))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: image_analysis_for_paths([image_path]))

    used = bot.load_quote_used_hashes([line + "\n" for line in reordered])

    assert used == {1}
    assert history.read_text(encoding="utf-8") == "[1]\n"
    with pytest.raises(bot.CorruptUsedHistoryError):
        bot.post_random_quote(used, set(), {})


def test_corrupt_current_used_history_does_not_reduce_to_stale_pickle(tmp_path: Path) -> None:
    json_path = tmp_path / "lines_used.json"
    pickle_path = tmp_path / "lines_used.pickle"
    json_path.write_text("{bad", encoding="utf-8")
    pickle_path.write_bytes(b"legacy pickle no longer trusted")

    with pytest.raises(bot.CorruptUsedHistoryError):
        bot.load_used_set(json_path, legacy_pickle_path=pickle_path)


def test_quote_hash_candidates_deduplicate_identical_source_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = ["Duplicate quote.", "Another quote.", "Duplicate quote."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(lines))

    candidates = bot.quote_candidates_for_current_cycle(set())

    assert [candidate["text"] for candidate in candidates].count("Duplicate quote.") == 1


def test_quote_cycle_resets_when_only_research_ineligible_source_records_remain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = ["Eligible first.", "Unresolved retained source.", "Eligible second."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    eligible_hashes = {
        bot.quote_text_hash(lines[0]),
        bot.quote_text_hash(lines[2]),
    }
    used = set(eligible_hashes)
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(lines))
    monkeypatch.setattr(bot, "completed_research_quote_hashes", lambda: eligible_hashes)

    candidates = bot.quote_candidates_for_current_cycle(used)

    assert used == set()
    assert {candidate["quote_hash"] for candidate in candidates} == eligible_hashes


def test_posting_duplicate_quote_marks_hash_and_blocks_identical_line_same_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    lines = ["Duplicate quote.", "Another quote.", "Duplicate quote."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "LINES_USED_FILE", tmp_path / "lines_used.json")
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", tmp_path / "images_used.json")
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", tmp_path / "regular_post_receipt.json")
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(lines))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: image_analysis_for_paths([image_path]))
    monkeypatch.setattr(bot.random, "uniform", lambda low, high: low)
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "950001"}}
        ),
    )
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "maybe_schedule_meme_after_quote_post", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    lines_used: set[str] = set()
    bot.post_random_quote(lines_used, set(), {})

    duplicate_hash = bot.quote_text_hash("Duplicate quote.")
    assert lines_used == {duplicate_hash}
    candidates = bot.quote_candidates_for_current_cycle(lines_used)
    assert [candidate["text"] for candidate in candidates] == ["Another quote."]


def test_quote_hash_history_migrates_legacy_lines_and_survives_reordering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_lines = ["First quote.", "Second quote."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(original_lines) + "\n", encoding="utf-8")
    history = tmp_path / "lines_used.json"
    history.write_text("[1]\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "LINES_USED_FILE", history)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(original_lines))

    used = bot.load_quote_used_hashes([line + "\n" for line in original_lines])
    second_hash = bot.quote_text_hash("Second quote.")
    assert used == {second_hash}

    reordered = ["Second quote.", "First quote.", "Brand new quote."]
    lines_file.write_text("\n".join(reordered) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(reordered))
    candidates = bot.quote_candidates_for_current_cycle(used)
    assert "Second quote." not in [candidate["text"] for candidate in candidates]
    assert "Brand new quote." in [candidate["text"] for candidate in candidates]


def test_image_metadata_is_used_only_when_current_content_matches(tmp_path: Path) -> None:
    image_path = tmp_path / "t01.jpg"
    image_path.write_bytes(b"original")
    analysis = image_analysis_for_paths([image_path], {"t01.jpg": {"description": "Original metadata"}})

    _, metadata = bot.image_metadata_for_basename(analysis, "t01.jpg", str(image_path))
    assert metadata == {"description": "Original metadata"}

    image_path.write_bytes(b"changed")
    with pytest.raises(bot.StaleImageMetadata):
        bot.image_metadata_for_basename(analysis, "t01.jpg", str(image_path))


def test_image_hash_cache_detects_changed_bytes_with_preserved_size_and_mtime(tmp_path: Path) -> None:
    image_path = tmp_path / "t01.jpg"
    image_path.write_bytes(b"abcdef")
    first = bot.current_image_sha256(str(image_path))
    stat = image_path.stat()
    image_path.write_bytes(b"ghijkl")
    os.utime(image_path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    second = bot.current_image_sha256(str(image_path))

    assert second != first


def test_stale_image_is_skipped_while_valid_image_remains_selectable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    stale_path = image_dir / "t01.jpg"
    valid_path = image_dir / "t02.jpg"
    stale_path.write_bytes(b"original")
    valid_path.write_bytes(b"valid")
    analysis = image_analysis_for_paths([stale_path, valid_path])
    stale_path.write_bytes(b"changed")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)

    chosen = bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["anything"]}}, {})

    assert chosen["basename"] == "t02.jpg"


def test_new_unanalysed_image_is_excluded_while_valid_image_remains_selectable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    used_path = image_dir / "t01.jpg"
    valid_path = image_dir / "t02.jpg"
    new_path = image_dir / "t03.jpg"
    used_path.write_bytes(b"used")
    valid_path.write_bytes(b"valid")
    analysis = image_analysis_for_paths([used_path, valid_path])
    new_path.write_bytes(b"new")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)

    chosen = bot.choose_matched_unused_image({"t01.jpg"}, {"analysis": {"primary_topics": ["anything"]}}, {})

    assert chosen["basename"] == "t02.jpg"


def test_all_stale_images_fail_without_mutating_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"original")
    analysis = image_analysis_for_paths([image_path])
    image_path.write_bytes(b"changed")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)
    used: set[str] = set()

    with pytest.raises(bot.NoEligibleImageForQuote):
        bot.choose_matched_unused_image(used, {"analysis": {"primary_topics": ["anything"]}}, {})

    assert used == set()


def test_missing_whole_quote_analysis_fails_regular_quote_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Christmas quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: None)

    with pytest.raises(RuntimeError, match="Quote analysis unavailable"):
        bot.choose_unused_line_candidate(set())


def test_unanalysed_current_christmas_quote_is_skipped_in_july(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = ["Ordinary quote.", "Christmas quote edited."]
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    analysed = quote_analysis_for_lines(["Ordinary quote.", "Old Christmas quote."])
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: analysed)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))

    candidates = bot.quote_candidates_for_current_cycle(set())

    assert [candidate["text"] for candidate in candidates] == ["Ordinary quote."]


def test_missing_image_analysis_fails_regular_image_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: None)

    with pytest.raises(bot.NoEligibleImageForQuote, match="Image analysis unavailable"):
        bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["government"]}}, {})


def test_successful_meme_post_persists_post_and_future_schedule_in_one_state_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    meme_path = meme_dir / "001_meme.png"
    meme_path.write_bytes(b"meme")
    saved_states: list[dict] = []
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "970001"}}
        ),
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: saved_states.append(json.loads(json.dumps(state))))
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    bot.post_next_meme(state)

    assert len(saved_states) == 1
    saved = saved_states[0]
    assert saved["last_main_post_id"] == "970001"
    assert saved["last_meme_post_epoch"] == 1_800_000_000
    assert "001_meme.png" in saved["posted_meme_filenames"]
    assert saved["next_meme_post_epoch"] > 1_800_000_000


@pytest.mark.parametrize("helper", ["cache_tweet", "record_recent_own_post"])
def test_meme_helper_failure_after_confirmation_leaves_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    helper: str,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "970001"}}
        ),
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, helper, lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(f"{helper} failed")))
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    assert bot.MEME_POST_RECEIPT_FILE.exists()
    assert json.loads(bot.MEME_POST_RECEIPT_FILE.read_text(encoding="utf-8"))["post_id"] == "970001"


def test_regular_post_commits_future_quote_schedule_and_receipt_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipts: list[dict] = []
    original_write = bot.write_regular_post_receipt
    monkeypatch.setattr(
        bot.random,
        "randint",
        lambda low, high: 7200 if low == bot.POST_SLEEP_MIN else low,
    )
    def capture_receipt(receipt: dict) -> None:
        if bot.regular_post_receipt_is_semantically_valid(receipt):
            receipts.append(json.loads(json.dumps(receipt)))
        original_write(receipt)

    monkeypatch.setattr(bot, "write_regular_post_receipt", capture_receipt)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["next_quote_post_epoch"] == 1_800_007_200
    assert receipts[0]["next_quote_post_epoch"] == 1_800_007_200


def test_regular_post_context_stage_runs_only_after_durable_main_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, *_ = configure_simple_quote_post(tmp_path, monkeypatch)
    saved = {"done": False}
    context_calls = []
    original_save = bot.save_regular_post_protected_state

    def tracked_save(*args, **kwargs):
        original_save(*args, **kwargs)
        saved["done"] = True

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
    packet = {"quote_id": quote_id, "quote_text": "Quote"}
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
    packet = {"quote_id": quote_id, "quote_text": "Quote"}
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
    assert events[-1]["formatter_version"] == context_module.HISTORICAL_CONTEXT_FORMATTER_V5
    assert events[-1]["rendering_mode"] == "public"


def test_already_completed_legacy_context_reply_is_not_relabelled_as_v4(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import historical_context_formatter as context_module

    quote_id = "a" * 64
    packet = {"quote_id": quote_id, "quote_text": "Quote"}
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


def test_regular_post_uses_confirmed_time_for_noon_meme_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    pre_confirm_epoch = int(datetime(2026, 7, 6, 11, 59, 50).timestamp())
    confirmed_epoch = int(datetime(2026, 7, 6, 12, 0, 5).timestamp())
    remote_confirmed = {"value": False}

    def fake_create_post(**kwargs: object) -> dict:
        # Model the remote acceptance boundary before the production helper
        # records its confirmation time.
        remote_confirmed["value"] = True
        result = mock_confirmed_main_post(
            kwargs,
            {"data": {"id": "950001"}},
        )
        return result

    def fake_now_epoch() -> int:
        return confirmed_epoch if remote_confirmed["value"] else pre_confirm_epoch

    def fake_randint(low: int, high: int) -> int:
        if low == bot.POST_SLEEP_MIN and high == bot.POST_SLEEP_MAX:
            return 7200
        return 3600

    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "MEME_TRIGGER_AFTER_HOUR", 12)
    monkeypatch.setattr(bot, "create_post", fake_create_post)
    monkeypatch.setattr(bot, "now_epoch", fake_now_epoch)
    monkeypatch.setattr(bot.random, "randint", fake_randint)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_quote_post_epoch"] == confirmed_epoch
    assert state["next_quote_post_epoch"] == confirmed_epoch + 7200
    assert state["next_meme_schedule_mode"] == "after_first_quote_after_midday"
    assert state["meme_anchor_quote_post_epoch"] == confirmed_epoch
    assert state["next_meme_post_epoch"] == confirmed_epoch + 3600


def test_regular_schedule_finalisation_failure_after_confirmation_is_confirmed_local_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    quote_hash = bot.quote_text_hash("Good quote.")
    original_materialize = bot.materialize_bound_regular_schedule_receipt
    monkeypatch.setattr(
        bot,
        "materialize_bound_regular_schedule_receipt",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("schedule finalisation failed")),
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    status, pending = bot.load_regular_post_receipt()
    assert status == "pending_schedule"
    assert pending is not None
    assert pending["post_id"] == "950001"
    assert quote_hash not in lines_used
    assert "t01.jpg" not in images_used
    assert "last_main_post_id" not in state

    expected = original_materialize(pending, _validate_result=False)
    monkeypatch.setattr(
        bot,
        "materialize_bound_regular_schedule_receipt",
        original_materialize,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: pytest.fail("confirmed pending receipt must not repost"),
    )
    bot.post_random_quote(lines_used, images_used, state)

    assert not receipt_file.exists()
    assert lines_used == set(expected["quote_history_after"])
    assert images_used == set(expected["image_history_after"])
    assert state["last_main_post_id"] == "950001"
    assert state["next_quote_post_epoch"] == expected["next_quote_post_epoch"]


def test_regular_quote_schedule_failure_after_confirmation_suppresses_quote_lane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    quote_hash = bot.quote_text_hash("Good quote.")
    monkeypatch.setattr(
        bot.random,
        "randint",
        lambda low, high: 7200 if low == bot.POST_SLEEP_MIN else low,
    )
    original_write = bot.write_regular_post_receipt
    def fail_final_schedule_receipt(receipt: dict) -> None:
        raise RuntimeError("quote schedule receipt failed")

    monkeypatch.setattr(
        bot,
        "write_regular_post_receipt",
        fail_final_schedule_receipt,
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    status, pending = bot.load_regular_post_receipt()
    assert status == "pending_schedule"
    assert pending is not None
    expected = bot.materialize_bound_regular_schedule_receipt(pending)
    assert expected["next_quote_post_epoch"] == 1_800_007_200
    assert quote_hash not in lines_used
    assert "t01.jpg" not in images_used

    monkeypatch.setattr(bot, "write_regular_post_receipt", original_write)
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: pytest.fail("confirmed pending receipt must not repost"),
    )
    bot.post_random_quote(lines_used, images_used, state)
    assert not receipt_file.exists()
    assert state["next_quote_post_epoch"] == 1_800_007_200
    assert state["last_main_post_id"] == "950001"


def test_regular_schedule_failure_replays_exact_bound_meme_delay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        lines_used,
        images_used,
        state,
        _lines_used_file,
        _images_used_file,
        receipt_file,
        _lines_file,
    ) = configure_simple_quote_post(tmp_path, monkeypatch)
    confirmed_epoch = int(datetime(2026, 7, 6, 13, 0, 0).timestamp())
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "MEME_TRIGGER_AFTER_HOUR", 12)
    monkeypatch.setattr(bot, "now_epoch", lambda: confirmed_epoch)

    def bound_delays(low: int, high: int) -> int:
        if low == bot.POST_SLEEP_MIN and high == bot.POST_SLEEP_MAX:
            return 7200
        assert (low, high) == (
            bot.MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS,
            bot.MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS,
        )
        return 3600

    monkeypatch.setattr(bot.random, "randint", bound_delays)
    original_materialize = bot.materialize_bound_regular_schedule_receipt
    monkeypatch.setattr(
        bot,
        "materialize_bound_regular_schedule_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("injected post-confirmation schedule failure")
        ),
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    status, pending = bot.load_regular_post_receipt()
    assert status == "pending_schedule"
    assert pending is not None
    assert pending["source_attempt"]["recovery_plan"]["meme_delay_seconds"] == 3600
    expected = original_materialize(pending, _validate_result=False)
    assert expected["next_meme_post_epoch"] == confirmed_epoch + 3600

    monkeypatch.setattr(
        bot,
        "materialize_bound_regular_schedule_receipt",
        original_materialize,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **_kwargs: pytest.fail(
            "local schedule replay must not create another regular post"
        ),
    )
    bot.post_random_quote(lines_used, images_used, state)

    assert not receipt_file.exists()
    assert state["next_quote_post_epoch"] == confirmed_epoch + 7200
    assert state["next_meme_post_epoch"] == confirmed_epoch + 3600
    assert state["next_meme_schedule_mode"] == "after_first_quote_after_midday"
    assert state["meme_anchor_quote_post_epoch"] == confirmed_epoch
    assert state["next_meme_post_epoch"] != confirmed_epoch + 1800


def test_regular_restart_replays_bound_meme_delay_from_receipt_after_state_save_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        lines_used,
        images_used,
        state,
        _lines_used_file,
        _images_used_file,
        receipt_file,
        _lines_file,
    ) = configure_simple_quote_post(tmp_path, monkeypatch)
    confirmed_epoch = int(datetime(2026, 7, 6, 13, 0, 0).timestamp())
    stale_due_epoch = confirmed_epoch - 60
    state.update(
        {
            "next_meme_post_epoch": stale_due_epoch,
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": bot.meme_schedule_date_str(
                stale_due_epoch
            ),
            "meme_anchor_quote_post_epoch": 0,
        }
    )
    bot.save_state(state, durable=True)
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "MEME_TRIGGER_AFTER_HOUR", 12)
    monkeypatch.setattr(bot, "now_epoch", lambda: confirmed_epoch)

    def bound_delays(low: int, high: int) -> int:
        if (low, high) == (bot.POST_SLEEP_MIN, bot.POST_SLEEP_MAX):
            return 7200
        assert (low, high) == (
            bot.MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS,
            bot.MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS,
        )
        return 3600

    monkeypatch.setattr(bot.random, "randint", bound_delays)
    original_save_protected = bot.save_regular_post_protected_state
    monkeypatch.setattr(
        bot,
        "save_regular_post_protected_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("injected protected-state save failure")
        ),
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    status, receipt = bot.load_regular_post_receipt()
    assert status == "valid"
    assert receipt is not None
    assert receipt["source_attempt"]["recovery_plan"]["meme_delay_seconds"] == 3600
    assert receipt["next_meme_post_epoch"] == confirmed_epoch + 3600

    restarted_state = bot.load_runtime_state()
    assert restarted_state["next_meme_post_epoch"] == stale_due_epoch
    monkeypatch.setattr(
        bot,
        "save_regular_post_protected_state",
        original_save_protected,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **_kwargs: pytest.fail(
            "startup receipt replay must not create another regular post"
        ),
    )

    reconciled = bot.reconcile_startup_main_post_receipts(
        set(),
        set(),
        restarted_state,
        confirmed_epoch + 1,
    )

    assert reconciled == {"regular": True, "meme": False}
    assert not receipt_file.exists()
    assert restarted_state["last_quote_post_epoch"] == confirmed_epoch
    assert restarted_state["next_meme_post_epoch"] == confirmed_epoch + 3600
    assert restarted_state["next_meme_schedule_mode"] == (
        "after_first_quote_after_midday"
    )
    assert restarted_state["meme_anchor_quote_post_epoch"] == confirmed_epoch
    assert restarted_state["next_meme_post_epoch"] != confirmed_epoch + 1800
    assert bot.load_runtime_state()["next_meme_post_epoch"] == (
        confirmed_epoch + 3600
    )


def test_schema_v2_regular_replay_does_not_invent_unbound_meme_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = valid_regular_receipt_v2(
        next_meme_post_epoch=0,
        next_meme_schedule_mode="",
        next_meme_schedule_date="",
        meme_anchor_quote_post_epoch=0,
        meme_schedule_changed_by_quote=False,
    )
    state = {
        "next_meme_post_epoch": 1_800_001_800,
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "next_meme_schedule_mode": "delayed_exception",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_001_800),
        "meme_anchor_quote_post_epoch": 0,
    }
    monkeypatch.setattr(
        bot,
        "maybe_schedule_meme_after_quote_post",
        lambda *_args, **_kwargs: pytest.fail(
            "schema-v2 replay must not derive a new random meme schedule"
        ),
    )

    bot.apply_regular_post_receipt(receipt, set(), set(), state)

    assert state["next_meme_post_epoch"] == 0
    assert state["next_meme_schedule_mode"] == ""
    assert state["next_meme_schedule_date"] == ""
    assert state["meme_anchor_quote_post_epoch"] == 0


def test_regular_receipt_replay_restores_future_quote_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt = {
        "schema_version": 1,
        "post_id": "950001",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    bot.atomic_write_json(receipt_file, receipt)

    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is True
    assert state["next_quote_post_epoch"] == 1_800_007_200


def test_startup_regular_receipt_replay_preserves_newer_production_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        _lines_used,
        _images_used,
        _state,
        _lines_used_file,
        _images_used_file,
        receipt_file,
        _lines_file,
    ) = configure_simple_quote_post(tmp_path, monkeypatch)
    quote_post_epoch = 1_784_680_936  # 2026-07-22 01:42:16 BST
    stale_receipt_next = 1_784_689_435  # 2026-07-22 04:03:55 BST
    newer_state_next = 1_784_714_624  # 2026-07-22 11:03:44 BST
    first_tick_epoch = 1_784_708_283  # 2026-07-22 09:18:03 BST
    receipt = valid_regular_receipt(
        post_id="2079728698731745489",
        image_basename="t24.jpg",
        quote_post_epoch=quote_post_epoch,
        next_quote_post_epoch=stale_receipt_next,
    )
    quote_hash = str(receipt["quote_hash"])
    bot.atomic_write_json(receipt_file, receipt)
    lines_used = {quote_hash}
    images_used = {"t24.jpg"}
    state = {
        "last_main_post_id": "2079728698731745489",
        "last_quote_post_epoch": quote_post_epoch,
        "last_regular_image_filename": "t24.jpg",
        "next_quote_post_epoch": newer_state_next,
    }
    monkeypatch.setattr(
        bot,
        "CONTROL_FILE",
        tmp_path / "missing-control.json",
    )
    monkeypatch.setattr(
        bot,
        "_CONTROL_CACHE",
        {"signature": None, "data": {}, "has_valid": False, "failure_signature": None},
    )
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **kwargs: {"status": "already_completed"},
    )
    monkeypatch.setattr(
        bot,
        "schedule_next_quote_post",
        lambda *args, **kwargs: pytest.fail("future production schedule must not be replaced"),
    )
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "glob", lambda pattern: [])
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "validate_generated_identity_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda lines: lines_used)
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda paths: images_used)
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "seed_recent_own_post_ids_from_cache", lambda state: None)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "now_epoch", lambda: first_tick_epoch)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state, key, current: (current, False),
    )
    monkeypatch.setattr(
        bot,
        "run_reply_lane_checks_for_tick",
        lambda state, current, last_reply, last_quote: (last_reply, last_quote),
    )
    monkeypatch.setattr(bot, "ambiguous_remote_post_is_blocking", lambda: False)
    monkeypatch.setattr(
        bot,
        "post_random_quote",
        lambda *args, **kwargs: pytest.fail("first loop tick must not post again"),
    )

    class FirstTickComplete(Exception):
        pass

    monkeypatch.setattr(
        bot,
        "sleep",
        lambda seconds: (_ for _ in ()).throw(FirstTickComplete),
    )

    with pytest.raises(FirstTickComplete):
        bot.main()

    assert state["next_quote_post_epoch"] == newer_state_next
    assert state["next_quote_post_epoch"] > first_tick_epoch
    assert not receipt_file.exists()


def test_startup_regular_receipt_with_missing_state_defers_first_quote_tick(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        lines_used,
        images_used,
        state,
        _lines_used_file,
        _images_used_file,
        receipt_file,
        _lines_file,
    ) = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt = valid_regular_receipt(
        quote_post_epoch=1_784_680_936,
        next_quote_post_epoch=1_784_689_435,
    )
    bot.atomic_write_json(receipt_file, receipt)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **kwargs: {"status": "completed"},
    )
    monkeypatch.setattr(bot.random, "randint", lambda low, high: 7_200)
    first_tick_epoch = 1_784_708_283

    status = bot.reconcile_main_post_receipts(
        lines_used,
        images_used,
        state,
        minimum_next_quote_epoch=first_tick_epoch,
    )

    assert state["last_quote_post_epoch"] == receipt["quote_post_epoch"]
    assert receipt["quote_hash"] in lines_used
    assert receipt["image_basename"] in images_used
    assert status == {"regular": True, "meme": False}
    assert state["next_quote_post_epoch"] == first_tick_epoch + 7_200
    assert state["next_quote_post_epoch"] > first_tick_epoch
    assert not receipt_file.exists()


def test_startup_receipt_persists_future_schedule_before_receipt_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        lines_used,
        images_used,
        state,
        _lines_used_file,
        _images_used_file,
        receipt_file,
        _lines_file,
    ) = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt = valid_regular_receipt(
        quote_post_epoch=1_784_680_936,
        next_quote_post_epoch=1_784_689_435,
    )
    bot.atomic_write_json(receipt_file, receipt)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **_kwargs: {"status": "completed"},
    )
    monkeypatch.setattr(bot.random, "randint", lambda _low, _high: 7_200)
    monkeypatch.setattr(
        bot,
        "remove_regular_post_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated removal crash")
        ),
    )
    first_tick_epoch = 1_784_708_283

    with pytest.raises(RuntimeError, match="simulated removal crash"):
        bot.reconcile_main_post_receipts(
            lines_used,
            images_used,
            state,
            minimum_next_quote_epoch=first_tick_epoch,
        )

    durable_state = json.loads(bot.STATE_FILE.read_text(encoding="utf-8"))
    assert durable_state["next_quote_post_epoch"] == first_tick_epoch + 7_200
    assert durable_state["next_quote_post_epoch"] > first_tick_epoch
    assert receipt_file.exists()


@pytest.mark.parametrize(
    "control_text",
    [
        json.dumps({"disable_all": True}),
        json.dumps({"pause_all": True}),
        "{",
    ],
)
def test_global_pause_leaves_startup_main_receipt_untouched(
    control_text: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        lines_used,
        images_used,
        state,
        _lines_used_file,
        _images_used_file,
        receipt_file,
        _lines_file,
    ) = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt = valid_regular_receipt(
        quote_post_epoch=1_784_680_936,
        next_quote_post_epoch=1_784_689_435,
    )
    bot.atomic_write_json(receipt_file, receipt)
    receipt_bytes = receipt_file.read_bytes()
    control_file = tmp_path / "mrsMThatcher.control.json"
    control_file.write_text(control_text, encoding="utf-8")
    monkeypatch.setattr(bot, "CONTROL_FILE", control_file)
    monkeypatch.setattr(
        bot,
        "_CONTROL_CACHE",
        {"signature": None, "data": {}, "has_valid": False, "failure_signature": None},
    )
    monkeypatch.setattr(
        bot,
        "reconcile_main_post_receipts",
        lambda *_args, **_kwargs: pytest.fail(
            "global maintenance pause must precede receipt reconciliation"
        ),
    )

    status = bot.reconcile_startup_main_post_receipts(
        lines_used,
        images_used,
        state,
        1_784_708_283,
    )

    assert status == {"regular": False, "meme": False}
    assert receipt_file.read_bytes() == receipt_bytes


def test_false_global_pause_preserves_startup_receipt_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_file = tmp_path / "mrsMThatcher.control.json"
    control_file.write_text(json.dumps({"disable_all": False}), encoding="utf-8")
    monkeypatch.setattr(bot, "CONTROL_FILE", control_file)
    monkeypatch.setattr(
        bot,
        "_CONTROL_CACHE",
        {"signature": None, "data": {}, "has_valid": False, "failure_signature": None},
    )
    expected = {"regular": True, "meme": False}
    monkeypatch.setattr(
        bot,
        "reconcile_main_post_receipts",
        lambda *_args, **_kwargs: expected,
    )

    assert bot.reconcile_startup_main_post_receipts(
        set(),
        set(),
        {},
        1_784_708_283,
    ) is expected


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_fresh_startup_with_uncertain_main_attempt_idles_without_remote_action(
    lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "require_production_bootstrap", lambda: None)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "reconcile_runtime_historical_context_state", lambda: None)
    monkeypatch.setattr(bot, "glob", lambda _pattern: [])
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "validate_generated_identity_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    state = {"next_quote_post_epoch": 1_800_007_200}
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "seed_recent_own_post_ids_from_cache", lambda _state: None)
    monkeypatch.setattr(bot, "save_state", lambda _state, **_kwargs: None)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: False)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (
            int(state_arg.get(key, current) or current),
            False,
        ),
    )

    if lane == "quote_image":
        attempt = bot.build_main_post_attempt(
            lane=lane,
            text="Good quote.",
            media_ids=["media-1"],
            made_with_ai=False,
            selected_identity={
                "quote_hash": bot.quote_text_hash("Good quote."),
                "line_no": 0,
                "source_line_number": 1,
                "image_basename": "t01.jpg",
                "image_no": 0,
            },
            recovery_plan={
                "quote_delay_seconds": bot.POST_SLEEP_MIN,
                "meme_delay_seconds": None,
                "meme_scheduling_enabled": False,
                "meme_trigger_after_hour": int(bot.MEME_TRIGGER_AFTER_HOUR),
                "meme_schedule_version": int(bot.MEME_SCHEDULE_VERSION),
                "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
                "meme_schedule_before": bot.bound_meme_schedule_state(
                    {},
                    schedule_timezone=bot.MAIN_POST_SCHEDULE_TIMEZONE,
                ),
                "quote_history_after": [
                    bot.quote_text_hash("Good quote."),
                ],
                "image_history_after": ["t01.jpg"],
            },
        )
        receipt_path = bot.REGULAR_POST_RECEIPT_FILE
    else:
        attempt = bot.build_main_post_attempt(
            lane=lane,
            text=bot.MEME_POST_TEXT,
            media_ids=["media-1"],
            made_with_ai=False,
            selected_identity={"meme_basename": "001_meme.png"},
            recovery_plan={
                "next_schedule_mode": "fallback",
                "meme_schedule_version": int(bot.MEME_SCHEDULE_VERSION),
                "fallback_hour": int(bot.MEME_FALLBACK_HOUR),
                "fallback_minute": int(bot.MEME_FALLBACK_MINUTE),
                "image_summary": "Unit meme image.",
                "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
            },
        )
        receipt_path = bot.MEME_POST_RECEIPT_FILE
    attempting = {**attempt, "lifecycle_state": "attempting"}
    bot.atomic_write_json(receipt_path, attempting, durable=True)
    receipt_bytes = receipt_path.read_bytes()

    remote_action = tmp_path / "unexpected-remote-action"

    def forbidden_remote_action(*_args: object, **_kwargs: object) -> object:
        remote_action.write_text("reached", encoding="utf-8")
        raise AssertionError("startup ambiguity pause must precede remote lanes")

    for name in (
        "x_request",
        "upload_media",
        "create_post",
        "post_random_quote",
        "post_next_meme",
        "run_reply_lane_checks_for_tick",
        "safely_process_due_historical_context_obligations",
    ):
        monkeypatch.setattr(bot, name, forbidden_remote_action)
    monkeypatch.setattr(bot, "sleep", lambda _seconds: os._exit(81))

    context = multiprocessing.get_context("fork")
    for _restart in range(2):
        process = context.Process(target=bot.main)
        process.start()
        process.join(timeout=10)
        assert process.exitcode == 81
        assert not remote_action.exists()
        assert receipt_path.read_bytes() == receipt_bytes


def test_main_global_pause_stops_before_every_remote_lane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    receipt_file = tmp_path / "regular_post_receipt.json"
    receipt_file.write_bytes(b'{"durable":"unchanged"}\n')
    receipt_bytes = receipt_file.read_bytes()
    control_file = tmp_path / "mrsMThatcher.control.json"
    control_file.write_text(json.dumps({"disable_all": True}), encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot, "CONTROL_FILE", control_file)
    monkeypatch.setattr(
        bot,
        "_CONTROL_CACHE",
        {"signature": None, "data": {}, "has_valid": False, "failure_signature": None},
    )
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "glob", lambda _pattern: [])
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "validate_generated_identity_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    state = {"next_quote_post_epoch": 0}
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "seed_recent_own_post_ids_from_cache", lambda _state: None)
    monkeypatch.setattr(bot, "save_state", lambda _state, **_kwargs: None)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_784_708_283)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda _state, _key, current: (current, False),
    )

    def remote_lane_reached(*_args, **_kwargs):
        pytest.fail("global maintenance pause must block every remote lane")

    monkeypatch.setattr(bot, "reconcile_main_post_receipts", remote_lane_reached)
    monkeypatch.setattr(bot, "run_reply_lane_checks_for_tick", remote_lane_reached)
    monkeypatch.setattr(bot, "post_random_quote", remote_lane_reached)
    monkeypatch.setattr(bot, "post_next_meme", remote_lane_reached)
    monkeypatch.setattr(bot, "create_post", remote_lane_reached)
    monkeypatch.setattr(bot, "upload_media", remote_lane_reached)
    monkeypatch.setattr(bot, "x_request", remote_lane_reached)
    monkeypatch.setattr(bot, "xai_structured_reply_call", remote_lane_reached)

    class MaintenanceTickComplete(Exception):
        pass

    monkeypatch.setattr(
        bot,
        "sleep",
        lambda seconds: (_ for _ in ()).throw(MaintenanceTickComplete),
    )

    with pytest.raises(MaintenanceTickComplete):
        bot.main()

    assert receipt_file.read_bytes() == receipt_bytes


def test_main_total_persistence_loss_latch_stops_later_remote_lanes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "reconcile_runtime_historical_context_state", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "glob", lambda _pattern: [])
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "list_meme_candidates", lambda: [])
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "validate_generated_identity_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    state = {
        "next_quote_post_epoch": 1,
        "next_meme_post_epoch": 1,
        "last_quote_post_epoch": 0,
    }
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "reconcile_startup_main_post_receipts", lambda *_args: None)
    monkeypatch.setattr(bot, "seed_recent_own_post_ids_from_cache", lambda _state: None)
    monkeypatch.setattr(bot, "save_state", lambda _state, **_kwargs: None)
    monkeypatch.setattr(bot, "ensure_meme_schedule_initialized", lambda _state: None)
    clock_must_not_run = False

    def controlled_clock() -> int:
        if clock_must_not_run:
            pytest.fail("the safety latch must be checked before the next clock read")
        return 1_784_708_283

    monkeypatch.setattr(bot, "now_epoch", controlled_clock)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (int(state_arg.get(key, current)), False),
    )

    context_ticks = 0
    reply_ticks = 0
    quote_attempts = 0

    def context_tick(*_args: object, **_kwargs: object) -> list[dict]:
        nonlocal context_ticks
        context_ticks += 1
        return []

    def reply_tick(
        _state: dict,
        _current: int,
        last_reply_check_epoch: int,
        last_quote_tweet_check_epoch: int,
    ) -> tuple[int, int]:
        nonlocal reply_ticks
        reply_ticks += 1
        return last_reply_check_epoch, last_quote_tweet_check_epoch

    def catastrophic_quote(*_args: object, **_kwargs: object) -> None:
        nonlocal clock_must_not_run, quote_attempts
        quote_attempts += 1
        bot.latch_confirmed_post_persistence_failure(
            lane="quote_image",
            post_id="950001",
            failure_components=["regular_post_receipt", "state"],
        )
        clock_must_not_run = True
        raise bot.UnrecoverableConfirmedPostPersistenceError("confirmed and unrepresented")

    monkeypatch.setattr(bot, "safely_process_due_historical_context_obligations", context_tick)
    monkeypatch.setattr(bot, "run_reply_lane_checks_for_tick", reply_tick)
    monkeypatch.setattr(bot, "post_random_quote", catastrophic_quote)
    monkeypatch.setattr(
        bot,
        "schedule_next_quote_post",
        lambda *_args, **_kwargs: pytest.fail(
            "an unrecoverable confirmed post must never be scheduled for retry"
        ),
    )
    monkeypatch.setattr(
        bot,
        "post_next_meme",
        lambda _state: pytest.fail("meme lane must not run after the safety latch"),
    )
    monkeypatch.setattr(
        bot,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("marker failed")),
    )

    class SafetyBarrierTickComplete(Exception):
        pass

    monkeypatch.setattr(
        bot,
        "sleep",
        lambda seconds: (_ for _ in ()).throw(SafetyBarrierTickComplete),
    )

    with pytest.raises(SafetyBarrierTickComplete):
        bot.main()

    assert quote_attempts == 1
    assert context_ticks == 1
    assert reply_ticks == 1
    assert bot.ambiguous_remote_post_is_blocking() is True
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()


@pytest.mark.parametrize(
    ("lane", "failure_kind"),
    [
        ("quote", "ambiguous"),
        ("meme", "ambiguous"),
        ("meme", "unrecoverable"),
    ],
)
def test_main_routes_remote_safety_failures_without_error_retry_bookkeeping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
    failure_kind: str,
) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "reconcile_runtime_historical_context_state", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "glob", lambda _pattern: [])
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "list_meme_candidates", lambda: [])
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "validate_generated_identity_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    current = 1_784_708_283
    state = {
        "next_quote_post_epoch": 1 if lane == "quote" else current + 3600,
        "next_meme_post_epoch": 1,
        "last_quote_post_epoch": 0,
    }
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "reconcile_startup_main_post_receipts", lambda *_args: None)
    monkeypatch.setattr(bot, "seed_recent_own_post_ids_from_cache", lambda _state: None)
    monkeypatch.setattr(bot, "save_state", lambda _state, **_kwargs: None)
    monkeypatch.setattr(bot, "ensure_meme_schedule_initialized", lambda _state: None)
    monkeypatch.setattr(bot, "now_epoch", lambda: current)
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: False)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (int(state_arg.get(key, current)), False),
    )
    monkeypatch.setattr(
        bot,
        "safely_process_due_historical_context_obligations",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        bot,
        "run_reply_lane_checks_for_tick",
        lambda _state, _current, reply_epoch, quote_epoch: (reply_epoch, quote_epoch),
    )
    monkeypatch.setattr(
        bot,
        "record_api_error",
        lambda *_args, **_kwargs: pytest.fail(
            "remote safety exceptions must not enter API-error bookkeeping"
        ),
    )
    monkeypatch.setattr(
        bot,
        "schedule_next_quote_post",
        lambda *_args, **_kwargs: pytest.fail(
            "remote safety exceptions must not schedule a quote retry"
        ),
    )
    monkeypatch.setattr(
        bot,
        "set_meme_delay_schedule",
        lambda *_args, **_kwargs: pytest.fail(
            "remote safety exceptions must not schedule a meme retry"
        ),
    )
    monkeypatch.setattr(
        bot,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("marker failed")),
    )

    def raise_safety_failure() -> None:
        if failure_kind == "ambiguous":
            bot.record_ambiguous_remote_post({"text": "confirmed or ambiguous"})
            raise bot.AmbiguousRemotePostOutcome("ambiguous", service="x")
        bot.latch_confirmed_post_persistence_failure(
            lane="daily_meme",
            post_id="970001",
            failure_components=["meme_post_receipt", "state"],
        )
        raise bot.UnrecoverableConfirmedPostPersistenceError(
            "confirmed and unrepresented"
        )

    if lane == "quote":
        monkeypatch.setattr(
            bot,
            "post_random_quote",
            lambda *_args, **_kwargs: raise_safety_failure(),
        )
        monkeypatch.setattr(
            bot,
            "post_next_meme",
            lambda _state: pytest.fail("meme lane must not run through the safety latch"),
        )
    else:
        monkeypatch.setattr(
            bot,
            "post_random_quote",
            lambda *_args, **_kwargs: pytest.fail("future quote lane must not run"),
        )
        monkeypatch.setattr(bot, "post_next_meme", lambda _state: raise_safety_failure())

    class SafetyBarrierTickComplete(Exception):
        pass

    monkeypatch.setattr(
        bot,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(SafetyBarrierTickComplete),
    )

    with pytest.raises(SafetyBarrierTickComplete):
        bot.main()

    assert bot.ambiguous_remote_post_is_blocking() is True
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()


def test_confirmed_persistence_marker_allows_controlled_startup_before_idle_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot.latch_confirmed_post_persistence_failure(
        lane="quote_image",
        post_id="950001",
        failure_components=["regular_post_receipt", "state"],
    )
    assert bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    order: list[str] = []
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: order.append("lock"))

    class StartupReachedBarrier(RuntimeError):
        pass

    def stop_after_startup_reconciliation() -> None:
        order.append("context_reconcile")
        raise StartupReachedBarrier

    monkeypatch.setattr(
        bot,
        "reconcile_runtime_historical_context_state",
        stop_after_startup_reconciliation,
    )

    with pytest.raises(StartupReachedBarrier):
        bot.main()
    assert order == ["lock", "context_reconcile"]
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="remote-write safety barrier",
    ):
        bot.block_if_ambiguous_remote_post()


def test_regular_receipt_replay_does_not_create_second_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt = {
        "schema_version": 1,
        "post_id": "950001",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    bot.atomic_write_json(receipt_file, receipt)
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called after receipt replay"))

    bot.post_random_quote(lines_used, images_used, state)

    assert not receipt_file.exists()
    assert state["next_quote_post_epoch"] == 1_800_007_200


def test_regular_post_state_failure_leaves_receipt_with_future_quote_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(
        bot.random,
        "randint",
        lambda low, high: 7200 if low == bot.POST_SLEEP_MIN else low,
    )
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: (_ for _ in ()).throw(OSError("state failed")))

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert receipt["next_quote_post_epoch"] == 1_800_007_200
    assert state["next_quote_post_epoch"] == 1_800_007_200


def test_regular_receipt_removal_failure_keeps_future_quote_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(
        bot.random,
        "randint",
        lambda low, high: 7200 if low == bot.POST_SLEEP_MIN else low,
    )
    monkeypatch.setattr(
        bot,
        "remove_regular_post_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("remove failed")),
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    assert state["next_quote_post_epoch"] == 1_800_007_200
    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]


def test_regular_post_invalid_non_numeric_post_id_restores_histories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(
        tmp_path,
        monkeypatch,
        create_response={"data": {"id": "banana"}},
    )

    with pytest.raises(RuntimeError, match="valid post id"):
        bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == set()
    assert images_used == set()
    assert "last_main_post_id" not in state


@pytest.mark.parametrize(
    ("failure", "expected_exception"),
    [
        ("global_image", bot.GlobalImageUnavailable),
        ("unsafe_image_migration", bot.UnsafeImageHistoryMigration),
        ("unexpected_image", ValueError),
        ("upload", OSError),
        ("create", OSError),
        ("invalid_post_id", RuntimeError),
    ],
)
def test_pre_confirmation_failures_restore_histories_after_quote_cycle_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_exception: type[Exception],
) -> None:
    lines_used = {"old-line"}
    images_used = {"old-image"}
    state: dict = {}
    monkeypatch.setattr(bot, "reconcile_main_post_receipts", lambda *args, **kwargs: {"regular": False, "meme": False})
    monkeypatch.setattr(bot, "quote_used_history_has_legacy_indices", lambda used: False)

    def reset_then_quote(used: set, *, excluded_quote_hashes: set[str] | None = None) -> dict:
        used.clear()
        return {"line_no": 0, "quote_hash": bot.quote_text_hash("Good quote."), "text": "Good quote.", "analysis": {}}

    monkeypatch.setattr(bot, "choose_unused_line_candidate", reset_then_quote)

    if failure == "global_image":
        monkeypatch.setattr(bot, "choose_matched_unused_image", lambda *args, **kwargs: (_ for _ in ()).throw(bot.GlobalImageUnavailable("no images")))
    elif failure == "unsafe_image_migration":
        monkeypatch.setattr(bot, "choose_matched_unused_image", lambda *args, **kwargs: (_ for _ in ()).throw(bot.UnsafeImageHistoryMigration("unsafe")))
    elif failure == "unexpected_image":
        monkeypatch.setattr(bot, "choose_matched_unused_image", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("scoring failed")))
    else:
        monkeypatch.setattr(
            bot,
            "choose_matched_unused_image",
            lambda *args, **kwargs: {"image_no": 0, "path": "image.jpg", "basename": "image.jpg", "score": 1.0},
        )
        if failure == "upload":
            monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: (_ for _ in ()).throw(OSError("upload failed")))
        else:
            monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
            monkeypatch.setattr(
                bot,
                "handoff_confirmed_media_upload_to_main_attempt",
                lambda _attempt, _authority: None,
            )
            if failure == "create":
                monkeypatch.setattr(bot, "create_post", lambda **kwargs: (_ for _ in ()).throw(OSError("create failed")))
            elif failure == "invalid_post_id":
                monkeypatch.setattr(
                    bot,
                    "create_post",
                    lambda **kwargs: mock_confirmed_main_post(
                        kwargs,
                        {"data": {"id": "banana"}},
                    ),
                )

    with pytest.raises(expected_exception):
        bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == {"old-line"}
    assert images_used == {"old-image"}
    assert "last_main_post_id" not in state



def test_daily_meme_missing_post_id_fails_without_success_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(kwargs, {"data": {}}),
    )
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: pytest.fail("save_state should not be called"))
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda event, **kwargs: events.append((event, kwargs)),
    )

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(RuntimeError, match="valid post id"):
        bot.post_next_meme(state)

    assert state == {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    assert events == [
        (
            "daily_meme_failure",
            {
                "status": "failed",
                "stage": "x_post_response_validation",
                "error_type": "RuntimeError",
                "reason": "Daily meme post did not return a valid post id; meme state unchanged",
            },
        )
    ]


def test_meme_post_uses_confirmed_time_across_midnight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)
    pre_confirm_epoch = int(datetime(2026, 7, 6, 23, 59, 50).timestamp())
    confirmed_epoch = int(datetime(2026, 7, 7, 0, 0, 5).timestamp())
    remote_confirmed = {"value": False}

    def fake_create_post(**kwargs: object) -> dict:
        remote_confirmed["value"] = True
        result = mock_confirmed_main_post(
            kwargs,
            {"data": {"id": "970001"}},
        )
        return result

    def fake_now_epoch() -> int:
        return confirmed_epoch if remote_confirmed["value"] else pre_confirm_epoch

    monkeypatch.setattr(bot, "create_post", fake_create_post)
    monkeypatch.setattr(bot, "now_epoch", fake_now_epoch)

    state = {"next_meme_post_epoch": pre_confirm_epoch, "posted_meme_filenames": []}
    bot.post_next_meme(state)

    assert state["last_meme_post_epoch"] == confirmed_epoch
    assert bot.epoch_date_str(state["last_meme_post_epoch"]) == "2026-07-07"
    assert state["posted_meme_filenames"] == ["001_meme.png"]
    assert state["next_meme_schedule_mode"] == "fallback"
    assert state["next_meme_post_epoch"] > confirmed_epoch
    assert bot.epoch_date_str(state["next_meme_post_epoch"]) == "2026-07-08"


def test_meme_same_local_date_barrier_suppresses_second_remote_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_epoch = int(datetime(2026, 7, 7, 9, 0, 0).timestamp())
    first_epoch = int(datetime(2026, 7, 7, 8, 0, 0).timestamp())
    monkeypatch.setattr(bot, "now_epoch", lambda: current_epoch)
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: pytest.fail(
            "a second meme on the confirmed local date must never reach X"
        ),
    )
    state = {
        "last_meme_post_epoch": first_epoch,
        "last_main_post_id": "970001",
        "next_meme_post_epoch": current_epoch,
        "posted_meme_filenames": ["001_meme.png"],
    }

    bot.post_next_meme(state)

    assert state["last_main_post_id"] == "970001"
    assert state["posted_meme_filenames"] == ["001_meme.png"]
    assert bot.epoch_date_str(state["next_meme_post_epoch"]) == "2026-07-08"
    assert state["next_meme_schedule_mode"] == "fallback"


def test_meme_schedule_finalisation_failure_after_confirmation_is_confirmed_local_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "970001"}}
        ),
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    original_materialize = bot.materialize_bound_meme_schedule_receipt
    monkeypatch.setattr(
        bot,
        "materialize_bound_meme_schedule_receipt",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("schedule finalisation failed")
        ),
    )
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    status, pending = bot.load_meme_post_receipt()
    assert status == "pending_schedule"
    assert pending is not None
    assert pending["post_id"] == "970001"
    assert state["posted_meme_filenames"] == []

    expected = original_materialize(pending, _validate_result=False)
    monkeypatch.setattr(
        bot,
        "materialize_bound_meme_schedule_receipt",
        original_materialize,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: pytest.fail("confirmed pending receipt must not repost"),
    )
    bot.post_next_meme(state)
    assert not bot.MEME_POST_RECEIPT_FILE.exists()
    assert state["last_main_post_id"] == "970001"
    assert state["last_meme_post_epoch"] == 1_800_000_000
    assert state["posted_meme_filenames"] == ["001_meme.png"]
    assert state["next_meme_post_epoch"] == expected["next_meme_post_epoch"]
    assert bot.epoch_date_str(state["next_meme_post_epoch"]) > bot.epoch_date_str(
        state["last_meme_post_epoch"]
    )


def test_confirmed_meme_state_failure_reconciles_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    receipt_file = tmp_path / "meme_post_receipt.json"
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "970001"}}
        ),
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: (_ for _ in ()).throw(OSError("state failed")))
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    # The confirmed journal remains a global remote-write barrier until the
    # failed local state transition is replayed and both durable records are
    # retired.
    assert bot.ambiguous_remote_post_is_blocking() is True
    assert receipt_file.exists()
    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert receipt["next_meme_post_epoch"] > 1_800_000_000
    assert state["next_meme_post_epoch"] > state["last_meme_post_epoch"]

    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    recovered: dict = {}
    assert bot.reconcile_meme_post_receipt(recovered) is True
    assert bot.reconcile_meme_post_receipt(recovered) is False
    assert recovered["last_main_post_id"] == "970001"
    assert recovered["posted_meme_filenames"] == ["001_meme.png"]
    assert recovered["next_meme_post_epoch"] == receipt["next_meme_post_epoch"]


def test_meme_restart_recovers_confirmation_after_state_save_failure_before_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, receipt_file = configure_simple_meme_post(tmp_path, monkeypatch)
    (bot.MEME_DIR / "002_meme.png").write_bytes(b"second meme")
    confirmed_epoch = 1_800_000_000
    bot.save_state(state, durable=True)
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs,
            {"data": {"id": "970001"}},
        ),
    )
    original_save_state = bot.save_state
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("injected confirmed-state save failure")
        ),
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    status, receipt = bot.load_meme_post_receipt()
    assert status == "valid"
    assert receipt is not None and receipt["post_id"] == "970001"
    restarted_state = bot.load_runtime_state()
    assert int(restarted_state.get("last_meme_post_epoch", 0) or 0) == 0

    monkeypatch.setattr(bot, "save_state", original_save_state)
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **_kwargs: pytest.fail(
            "restart must not create a second meme on the confirmed local date"
        ),
    )
    reconciled = bot.reconcile_startup_main_post_receipts(
        set(),
        set(),
        restarted_state,
        confirmed_epoch,
    )

    assert reconciled == {"regular": False, "meme": True}
    assert not receipt_file.exists()
    assert restarted_state["last_main_post_id"] == "970001"
    assert restarted_state["last_meme_post_epoch"] == confirmed_epoch
    assert restarted_state["posted_meme_filenames"] == ["001_meme.png"]
    assert bot.load_runtime_state()["last_meme_post_epoch"] == confirmed_epoch

    bot.post_next_meme(restarted_state)

    assert restarted_state["last_main_post_id"] == "970001"
    assert restarted_state["posted_meme_filenames"] == ["001_meme.png"]
    assert bot.meme_schedule_date_str(
        restarted_state["next_meme_post_epoch"]
    ) > bot.meme_schedule_date_str(confirmed_epoch)


def test_meme_receipt_replay_does_not_create_second_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_file = tmp_path / "meme_post_receipt.json"
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", receipt_file)
    bot.atomic_write_json(
        receipt_file,
        {
            "schema_version": 1,
            "post_id": "970001",
            "meme_basename": "001_meme.png",
            "meme_post_epoch": 1_800_000_000,
            "next_meme_post_epoch": 1_800_086_400,
        },
    )
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called after meme receipt replay"))

    state: dict = {}
    bot.post_next_meme(state)

    assert not receipt_file.exists()
    assert state["posted_meme_filenames"] == ["001_meme.png"]
    assert state["next_meme_post_epoch"] == 1_800_086_400


def test_confirmed_meme_receipt_write_failure_keeps_normal_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    (meme_dir / "002_meme.png").write_bytes(b"second meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "970001"}}
        ),
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    original_write = bot.write_meme_post_receipt
    monkeypatch.setattr(bot, "write_meme_post_receipt", lambda receipt: (_ for _ in ()).throw(OSError("receipt failed")))
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError) as caught:
        bot.post_next_meme(state)

    assert type(caught.value) is bot.ConfirmedPostLocalPersistenceError
    status, pending = bot.load_meme_post_receipt()
    assert status == "pending_schedule"
    assert pending is not None and pending["post_id"] == "970001"
    assert state["posted_meme_filenames"] == []
    assert "last_meme_post_epoch" not in state
    monkeypatch.setattr(
        bot,
        "write_meme_post_receipt",
        original_write,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **_kwargs: pytest.fail(
            "same-date restart must not post the second available meme"
        ),
    )
    bot.post_next_meme(state)
    assert state["posted_meme_filenames"] == ["001_meme.png"]


def test_confirmed_meme_sigint_is_delivered_only_after_durable_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    receipt_file = tmp_path / "meme_post_receipt.json"
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda _path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "970001"}}
        ),
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)
    stages: list[str] = []
    original_write = bot.write_meme_post_receipt

    guard_token = object()

    def begin_deferral() -> object:
        stages.append("begin")
        return guard_token

    def write_receipt(receipt: dict) -> None:
        stages.append("receipt")
        original_write(receipt)

    def deliver_pending_sigint(guard: object | None) -> None:
        assert guard is guard_token
        assert receipt_file.exists()
        stages.append("end")
        raise KeyboardInterrupt

    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", begin_deferral)
    monkeypatch.setattr(bot, "write_meme_post_receipt", write_receipt)
    monkeypatch.setattr(bot, "end_confirmed_post_sigint_deferral", deliver_pending_sigint)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(KeyboardInterrupt):
        bot.post_next_meme(state)

    assert stages == ["begin", "receipt", "end"]
    assert json.loads(receipt_file.read_text(encoding="utf-8"))["post_id"] == "970001"
    assert bot.ambiguous_remote_post_is_blocking() is True


def test_meme_hard_death_after_remote_acceptance_leaves_restart_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    state, receipt_file = configure_simple_meme_post(tmp_path, monkeypatch)
    remote_acceptance = tmp_path / "meme-remote-accepted"

    def accept_then_hard_exit(
        method: str,
        path: str,
        **_kwargs: object,
    ) -> dict:
        assert method == "POST"
        assert path == "/2/tweets"
        assert receipt_file.exists()
        status, attempt = bot.load_meme_post_receipt()
        assert status == "sending"
        assert attempt is not None
        assert attempt["lifecycle_state"] == "attempting"
        descriptor = os.open(
            remote_acceptance,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, b"accepted")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os._exit(74)

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(monkeypatch, accept_then_hard_exit)
    context = multiprocessing.get_context("fork")
    process = context.Process(target=bot.post_next_meme, args=(state,))
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 74
    assert remote_acceptance.read_bytes() == b"accepted"

    status, attempt = bot.load_meme_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lane"] == "daily_meme"
    assert attempt["lifecycle_state"] == "attempting"
    assert attempt["selected_identity"] == {"meme_basename": "001_meme.png"}
    assert attempt["recovery_plan"] == {
        "next_schedule_mode": "fallback",
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "fallback_hour": bot.MEME_FALLBACK_HOUR,
        "fallback_minute": bot.MEME_FALLBACK_MINUTE,
        "image_summary": (
            "Anti-socialist meme image. Original filename: 001_meme.png."
        ),
        "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
    }
    assert bot.ambiguous_remote_post_is_blocking() is True

    retry_calls = 0

    def forbidden_retry(**_kwargs: object) -> dict:
        nonlocal retry_calls
        retry_calls += 1
        return {"data": {"id": "970002"}}

    monkeypatch.setattr(bot, "create_post", forbidden_retry)
    for _restart in range(2):
        with pytest.raises(bot.AmbiguousRemotePostOutcome):
            bot.post_next_meme(state)
    assert retry_calls == 0


def test_meme_generic_4xx_retains_sending_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    state, _receipt_file = configure_simple_meme_post(tmp_path, monkeypatch)

    def definite_failure(
        method: str,
        path: str,
        **_kwargs: object,
    ) -> dict:
        assert method == "POST"
        assert path == "/2/tweets"
        status, attempt = bot.load_meme_post_receipt()
        assert status == "sending"
        assert attempt is not None
        assert attempt["lifecycle_state"] == "attempting"
        raise bot.ApiError(
            "definite rejection",
            service="x",
            status_code=400,
            request_method="POST",
            request_path="/2/tweets",
        )

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(monkeypatch, definite_failure)
    with pytest.raises(bot.ApiError, match="definite rejection"):
        bot.post_next_meme(state)

    status, attempt = bot.load_meme_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lifecycle_state"] == "attempting"
    assert bot.ambiguous_remote_post_is_blocking() is True


@pytest.mark.parametrize(
    "failure_kind",
    ["http_500", "status_unknown", "unexpected", "transport"],
)
def test_meme_uncertain_remote_failure_retains_attempt_and_blocks_retry(
    failure_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    actual_x_request = bot.x_request
    state, _receipt_file = configure_simple_meme_post(tmp_path, monkeypatch)
    attempts = 0

    def uncertain_request(
        _method: str,
        _path: str,
        **_kwargs: object,
    ) -> dict:
        nonlocal attempts
        attempts += 1
        if failure_kind == "http_500":
            raise bot.ApiError("uncertain 500", service="x", status_code=500)
        if failure_kind == "status_unknown":
            raise bot.ApiError("unknown status", service="x")
        raise RuntimeError("unexpected transport wrapper failure")

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    if failure_kind == "transport":
        monkeypatch.setattr(bot, "x_request", actual_x_request)

        def transport_failure(*_args: object, **_kwargs: object) -> object:
            nonlocal attempts
            attempts += 1
            raise bot.requests.ConnectionError("connection dropped")

        monkeypatch.setattr(bot.requests, "request", transport_failure)
        expected_error: type[BaseException] = bot.AmbiguousRemotePostOutcome
    else:
        install_receipt_bound_x_request_stub(monkeypatch, uncertain_request)
        expected_error = (
            RuntimeError
            if failure_kind == "unexpected"
            else bot.ApiError
        )

    with pytest.raises(expected_error):
        bot.post_next_meme(state)

    status, attempt = bot.load_meme_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lifecycle_state"] == "attempting"
    assert attempts == 1

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_next_meme(state)
    assert attempts == 1


def test_meme_normal_success_atomically_promotes_sending_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    state, receipt_file = configure_simple_meme_post(tmp_path, monkeypatch)
    observed_attempt: dict = {}

    def confirmed_create(
        method: str,
        path: str,
        **_kwargs: object,
    ) -> dict:
        assert method == "POST"
        assert path == "/2/tweets"
        status, attempt = bot.load_meme_post_receipt()
        assert status == "sending"
        assert attempt is not None
        assert attempt["lifecycle_state"] == "attempting"
        observed_attempt.update(attempt)
        return {"data": {"id": "970001"}}

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(monkeypatch, confirmed_create)
    monkeypatch.setattr(
        bot, "remove_meme_post_receipt", lambda *_args, **_kwargs: None
    )
    bot.post_next_meme(state)

    status, confirmed = bot.load_meme_post_receipt()
    assert status == "valid"
    assert confirmed is not None
    assert confirmed["post_id"] == "970001"
    assert confirmed["attempt_id"] == observed_attempt["attempt_id"]
    assert (
        confirmed["attempt_payload_sha256"]
        == observed_attempt["payload_sha256"]
    )
    assert receipt_file.exists()


def test_meme_ambiguous_create_without_marker_uses_durable_attempt_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_atomic_write = bot.atomic_write_json
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda _path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    def ambiguous_create(**kwargs: object) -> dict:
        mock_confirmed_main_post(kwargs, {})
        bot.record_ambiguous_remote_post({"text": bot.MEME_POST_TEXT})
        raise bot.AmbiguousRemotePostOutcome("ambiguous", service="x")

    monkeypatch.setattr(bot, "create_post", ambiguous_create)
    def fail_only_ambiguous_marker(
        path: Path,
        value: object,
        **kwargs: object,
    ) -> None:
        if Path(path) == bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE:
            raise OSError("marker failed")
        original_atomic_write(path, value, **kwargs)

    monkeypatch.setattr(bot, "atomic_write_json", fail_only_ambiguous_marker)
    guard_token = object()
    ended_guards: list[object | None] = []
    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", lambda: guard_token)
    monkeypatch.setattr(
        bot,
        "end_confirmed_post_sigint_deferral",
        lambda guard: ended_guards.append(guard),
    )

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_next_meme(state)

    assert bot.ambiguous_remote_post_is_blocking() is True
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert ended_guards == [guard_token]


def test_meme_emergency_canonical_state_survives_backup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda _path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "970001"}}
        ),
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt failed")),
    )
    monkeypatch.setattr(
        bot,
        "write_latest_state_backup",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("backup failed")),
    )
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError) as caught:
        bot.post_next_meme(state)

    assert type(caught.value) is bot.ConfirmedPostLocalPersistenceError
    assert bot.json_file_matches(bot.STATE_FILE, state) is True
    assert bot.ambiguous_remote_post_is_blocking() is False


def test_meme_total_persistence_loss_latches_all_remote_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    remote_calls = 0
    original_create_post = bot.create_post

    def confirmed_create(**kwargs: object) -> dict:
        nonlocal remote_calls
        remote_calls += 1
        return mock_confirmed_main_post(
            kwargs,
            {"data": {"id": "970001"}},
        )

    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda _path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(bot, "create_post", confirmed_create)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt failed")),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("state failed")),
    )
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.UnrecoverableConfirmedPostPersistenceError):
        bot.post_next_meme(state)

    assert remote_calls == 1
    assert bot.ambiguous_remote_post_is_blocking() is True
    marker = json.loads(bot.AMBIGUOUS_POST_OUTCOME_FILE.read_text(encoding="utf-8"))
    assert marker["outcome"] == "confirmed_remote_post_local_persistence_failed"
    assert marker["lane"] == "daily_meme"
    assert marker["post_id"] == "970001"
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    assert bot.ambiguous_remote_post_is_blocking() is True
    boundary_calls = 0

    def unexpected_boundary(*_args: object, **_kwargs: object) -> dict:
        nonlocal boundary_calls
        boundary_calls += 1
        pytest.fail("remote boundary must not be reached after the durable safety marker")

    install_receipt_bound_x_request_stub(monkeypatch, unexpected_boundary)
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        original_create_post("must not be sent")
    assert remote_calls == 1
    assert boundary_calls == 0


def test_meme_total_persistence_and_marker_loss_uses_durable_attempt_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_atomic_write = bot.atomic_write_json
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda _path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "970001"}}
        ),
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt failed")),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("state failed")),
    )
    def fail_only_ambiguous_marker(
        path: Path,
        value: object,
        **kwargs: object,
    ) -> None:
        if Path(path) == bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE:
            raise OSError("marker failed")
        original_atomic_write(path, value, **kwargs)

    monkeypatch.setattr(bot, "atomic_write_json", fail_only_ambiguous_marker)
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)
    guard_token = object()
    ended_guards: list[object | None] = []
    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", lambda: guard_token)
    monkeypatch.setattr(
        bot,
        "end_confirmed_post_sigint_deferral",
        lambda guard: ended_guards.append(guard),
    )

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.UnrecoverableConfirmedPostPersistenceError):
        bot.post_next_meme(state)

    assert bot.ambiguous_remote_post_is_blocking() is True
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert ended_guards == [guard_token]


def test_confirmed_meme_with_incomplete_emergency_state_latches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda _path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs,
            {"data": {"id": "970001"}},
        ),
    )
    clock_calls = 0

    def clock_fails_after_attempt_persistence() -> int:
        nonlocal clock_calls
        clock_calls += 1
        if clock_calls == 1:
            return 1_800_000_000
        raise RuntimeError("clock failed after confirmation")

    monkeypatch.setattr(bot, "now_epoch", clock_fails_after_attempt_persistence)
    saved_states: list[dict] = []
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda state, **_kwargs: saved_states.append(json.loads(json.dumps(state))),
    )
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    state = {
        "last_main_post_id": "960000",
        "last_meme_post_epoch": 1_700_000_000,
        "next_meme_post_epoch": 1_700_086_400,
        "posted_meme_filenames": [],
    }
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    assert saved_states == []
    status, receipt = bot.load_meme_post_receipt()
    assert status == "valid"
    assert receipt is not None
    assert receipt["post_id"] == "970001"
    assert bot.ambiguous_remote_post_is_blocking() is True
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()


def test_meme_receipt_removal_failure_keeps_future_meme_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "970001"}}
        ),
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(
        bot,
        "remove_meme_post_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("remove failed")),
    )
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    assert state["next_meme_post_epoch"] > state["last_meme_post_epoch"]
    assert state["meme_anchor_quote_post_epoch"] == 0


def test_test_post_quote_reports_confirmed_local_failure_distinctly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: {})
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda images: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(
        bot,
        "post_random_quote",
        lambda *args, **kwargs: (_ for _ in ()).throw(bot.ConfirmedPostLocalPersistenceError("confirmed")),
    )
    saved: list[dict] = []
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: saved.append(dict(state)))

    assert bot.run_test_post_quote() == 3
    assert saved == [{}]


def test_one_shot_quote_unrecoverable_failure_cannot_exit_on_memory_latch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: {})
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _images: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(
        bot,
        "post_random_quote",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            bot.UnrecoverableConfirmedPostPersistenceError("confirmed")
        ),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: pytest.fail(
            "unrecoverable confirmed post must not use ordinary exit bookkeeping"
        ),
    )
    lanes: list[str] = []

    class ProcessHeld(Exception):
        pass

    def hold(*, lane: str) -> None:
        lanes.append(lane)
        raise ProcessHeld

    monkeypatch.setattr(bot, "wait_for_durable_barrier_before_one_shot_exit", hold)

    with pytest.raises(ProcessHeld):
        bot.run_test_post_quote()

    assert lanes == ["quote_image"]


def test_test_post_quote_migrates_old_meme_schedule_before_post_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    state = {
        "meme_schedule_version": 1,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    seen_states: list[dict] = []
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda images: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "post_random_quote", lambda lines_used, images_used, state: seen_states.append(json.loads(json.dumps(state))))

    assert bot.run_test_post_quote() == 0
    assert seen_states[0]["meme_schedule_version"] == bot.MEME_SCHEDULE_VERSION
    assert seen_states[0]["next_meme_schedule_mode"] == "fallback_migrated"


def test_test_post_quote_preserves_current_meme_schedule_preflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    state = {
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    before = json.loads(json.dumps(state))
    seen_states: list[dict] = []
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda images: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "post_random_quote", lambda lines_used, images_used, state: seen_states.append(json.loads(json.dumps(state))))

    assert bot.run_test_post_quote() == 0
    assert seen_states[0] == before


def test_test_post_quote_load_failure_happens_before_post_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: (_ for _ in ()).throw(RuntimeError("future meme schedule version")))
    monkeypatch.setattr(bot, "post_random_quote", lambda *args, **kwargs: pytest.fail("post_random_quote should not be called"))

    with pytest.raises(RuntimeError, match="future meme schedule version"):
        bot.run_test_post_quote()


def test_test_post_quote_receipt_replay_does_not_create_second_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.atomic_write_json(receipt_file, valid_regular_receipt())
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda lines: lines_used)
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda images: images_used)
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called after receipt replay"))

    assert bot.run_test_post_quote() == 0
    assert not receipt_file.exists()
    assert state["last_main_post_id"] == "950001"


def test_test_post_meme_reports_confirmed_local_failure_distinctly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: {})
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        bot,
        "post_next_meme",
        lambda *args, **kwargs: (_ for _ in ()).throw(bot.ConfirmedPostLocalPersistenceError("confirmed")),
    )
    saved: list[dict] = []
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: saved.append(dict(state)))

    assert bot.run_test_post_meme() == 3
    assert saved == [{}]


def test_one_shot_meme_ambiguity_cannot_exit_on_memory_latch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: {})
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        bot,
        "post_next_meme",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            bot.AmbiguousRemotePostOutcome("ambiguous", service="x")
        ),
    )
    monkeypatch.setattr(
        bot,
        "record_api_error",
        lambda *_args, **_kwargs: pytest.fail(
            "ambiguous outcome must not enter ordinary API-error bookkeeping"
        ),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: pytest.fail(
            "ambiguous outcome must not use ordinary exit bookkeeping"
        ),
    )
    lanes: list[str] = []

    class ProcessHeld(Exception):
        pass

    def hold(*, lane: str) -> None:
        lanes.append(lane)
        raise ProcessHeld

    monkeypatch.setattr(bot, "wait_for_durable_barrier_before_one_shot_exit", hold)

    with pytest.raises(ProcessHeld):
        bot.run_test_post_meme()

    assert lanes == ["daily_meme"]


def test_one_shot_memory_only_barrier_waits_for_durable_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", True)

    class ProcessHeld(Exception):
        pass

    monkeypatch.setattr(
        bot,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(ProcessHeld),
    )

    with pytest.raises(ProcessHeld):
        bot.wait_for_durable_barrier_before_one_shot_exit(lane="quote_image")


def test_test_main_tick_stops_after_reply_safety_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    sending = unit_sending_reply_receipt()
    waited: list[str] = []

    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_000)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (int(state_arg.get(key, 0) or 0), False),
    )

    def trigger_reply_barrier(*_args: object, **_kwargs: object) -> tuple[int, int]:
        bot.write_sending_reply_receipt(sending)
        return 0, 0

    monkeypatch.setattr(bot, "run_reply_lane_checks_for_tick", trigger_reply_barrier)
    monkeypatch.setattr(
        bot,
        "wait_for_durable_barrier_before_one_shot_exit",
        lambda *, lane: waited.append(lane),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: pytest.fail(
            "one-shot tick must not perform ordinary completion save after barrier"
        ),
    )

    assert bot.run_test_main_tick() == 0
    assert waited == ["production_reply_tick"]
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)


@pytest.mark.parametrize(
    ("priority", "first_lane", "failure_type"),
    [
        ("normal", "normal", bot.AmbiguousRemotePostOutcome),
        (
            "normal",
            "normal",
            bot.UnrecoverableConfirmedReplyPersistenceError,
        ),
        ("quote", "quote_tweet", bot.AmbiguousRemotePostOutcome),
        (
            "quote",
            "quote_tweet",
            bot.UnrecoverableConfirmedReplyPersistenceError,
        ),
    ],
)
def test_production_reply_tick_stops_sibling_lane_on_safety_failure(
    monkeypatch: pytest.MonkeyPatch,
    priority: str,
    first_lane: str,
    failure_type: type[BaseException],
) -> None:
    state = bot.default_state()
    state.update(
        {
            "next_reply_lane_priority": priority,
            "last_reply_epoch": 0,
            "last_reply_check_epoch": 0,
            "last_quote_tweet_check_epoch": 0,
        }
    )
    sending = unit_sending_reply_receipt(
        lane="quote_tweet" if first_lane == "quote_tweet" else "mention",
    )
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "REPLY_CHECK_EVERY_SECONDS", 1)
    monkeypatch.setattr(bot, "QUOTE_CHECK_EVERY_SECONDS", 1)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (int(state_arg.get(key, 0) or 0), False),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: pytest.fail(
            "reply safety failure must not consume or save a scheduler interval"
        ),
    )

    def safety_failure(_state: dict) -> str:
        bot.write_sending_reply_receipt(sending)
        if issubclass(failure_type, bot.ApiError):
            raise failure_type("reply safety failure", service="x")
        raise failure_type("reply safety failure")

    def later_lane(_state: dict) -> str:
        pytest.fail("the sibling reply lane must not run after a safety failure")

    monkeypatch.setattr(
        bot,
        "maybe_reply_to_mentions",
        safety_failure if first_lane == "normal" else later_lane,
    )
    monkeypatch.setattr(
        bot,
        "maybe_reply_to_quote_tweets",
        safety_failure if first_lane == "quote_tweet" else later_lane,
    )

    assert bot.run_reply_lane_checks_for_tick(state, 100, 0, 0) == (0, 0)
    assert bot.ambiguous_remote_post_is_blocking() is True
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)


@pytest.mark.parametrize(
    ("priority", "first_lane", "failure_type"),
    [
        ("normal", "normal", bot.AmbiguousRemotePostOutcome),
        (
            "normal",
            "normal",
            bot.UnrecoverableConfirmedReplyPersistenceError,
        ),
        ("quote", "quote_tweet", bot.AmbiguousRemotePostOutcome),
        (
            "quote",
            "quote_tweet",
            bot.UnrecoverableConfirmedReplyPersistenceError,
        ),
    ],
)
def test_main_reply_safety_failure_reaches_top_of_loop_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    priority: str,
    first_lane: str,
    failure_type: type[BaseException],
) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    current = 1_784_708_283
    state = bot.default_state()
    state.update(
        {
            "next_reply_lane_priority": priority,
            "last_reply_epoch": 0,
            "last_reply_check_epoch": 0,
            "last_quote_tweet_check_epoch": 0,
            "next_quote_post_epoch": 0,
            "next_meme_post_epoch": 0,
            "last_quote_post_epoch": 0,
        }
    )
    sending = unit_sending_reply_receipt(
        lane="quote_tweet" if first_lane == "quote_tweet" else "mention",
    )
    clock_must_not_run = False
    context_ticks = 0
    reply_attempts = 0

    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "reconcile_runtime_historical_context_state", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "glob", lambda _pattern: [])
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "REPLY_CHECK_EVERY_SECONDS", 1)
    monkeypatch.setattr(bot, "QUOTE_CHECK_EVERY_SECONDS", 1)
    monkeypatch.setattr(bot, "list_meme_candidates", lambda: [])
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "validate_generated_identity_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "reconcile_startup_main_post_receipts", lambda *_args: None)
    monkeypatch.setattr(bot, "seed_recent_own_post_ids_from_cache", lambda _state: None)
    monkeypatch.setattr(bot, "save_state", lambda _state, **_kwargs: None)
    monkeypatch.setattr(bot, "ensure_meme_schedule_initialized", lambda _state: None)
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: False)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (int(state_arg.get(key, 0) or 0), False),
    )

    def controlled_clock() -> int:
        if clock_must_not_run:
            pytest.fail("the top-of-loop barrier must run before another clock read")
        return current

    def context_tick(*_args: object, **_kwargs: object) -> list[dict]:
        nonlocal context_ticks
        context_ticks += 1
        return []

    def safety_failure(_state: dict) -> str:
        nonlocal clock_must_not_run, reply_attempts
        reply_attempts += 1
        bot.write_sending_reply_receipt(sending)
        clock_must_not_run = True
        if issubclass(failure_type, bot.ApiError):
            raise failure_type("reply safety failure", service="x")
        raise failure_type("reply safety failure")

    def later_lane(_state: dict) -> str:
        pytest.fail("the sibling reply lane must not run after a safety failure")

    monkeypatch.setattr(bot, "now_epoch", controlled_clock)
    monkeypatch.setattr(
        bot,
        "safely_process_due_historical_context_obligations",
        context_tick,
    )
    monkeypatch.setattr(
        bot,
        "maybe_reply_to_mentions",
        safety_failure if first_lane == "normal" else later_lane,
    )
    monkeypatch.setattr(
        bot,
        "maybe_reply_to_quote_tweets",
        safety_failure if first_lane == "quote_tweet" else later_lane,
    )
    monkeypatch.setattr(
        bot,
        "post_random_quote",
        lambda *_args, **_kwargs: pytest.fail(
            "main quote lane must not run after a reply safety failure"
        ),
    )
    monkeypatch.setattr(
        bot,
        "post_next_meme",
        lambda _state: pytest.fail(
            "meme lane must not run after a reply safety failure"
        ),
    )

    class SafetyBarrierTickComplete(Exception):
        pass

    monkeypatch.setattr(
        bot,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(SafetyBarrierTickComplete),
    )

    with pytest.raises(SafetyBarrierTickComplete):
        bot.main()

    assert reply_attempts == 1
    assert context_ticks == 1
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)
    assert bot.ambiguous_remote_post_is_blocking() is True


@pytest.mark.parametrize(
    ("priority", "first_lane", "failure_type", "expected_wait_lane"),
    [
        (
            "normal",
            "normal",
            bot.AmbiguousRemotePostOutcome,
            "normal_reply",
        ),
        (
            "normal",
            "normal",
            bot.UnrecoverableConfirmedReplyPersistenceError,
            "normal_reply",
        ),
        (
            "quote",
            "quote_tweet",
            bot.AmbiguousRemotePostOutcome,
            "quote_tweet_reply",
        ),
        (
            "quote",
            "quote_tweet",
            bot.UnrecoverableConfirmedReplyPersistenceError,
            "quote_tweet_reply",
        ),
    ],
)
def test_test_cycle_reply_safety_failure_stops_later_lane(
    monkeypatch: pytest.MonkeyPatch,
    priority: str,
    first_lane: str,
    failure_type: type[BaseException],
    expected_wait_lane: str,
) -> None:
    state = bot.default_state()
    state["next_reply_lane_priority"] = priority
    sending = unit_sending_reply_receipt(
        lane="quote_tweet" if first_lane == "quote_tweet" else "mention",
    )
    waited: list[str] = []

    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: pytest.fail(
            "test cycle must not perform a final save after reply safety failure"
        ),
    )
    monkeypatch.setattr(
        bot,
        "wait_for_durable_barrier_before_one_shot_exit",
        lambda *, lane: waited.append(lane),
    )

    def safety_failure(_state: dict) -> str:
        bot.write_sending_reply_receipt(sending)
        if issubclass(failure_type, bot.ApiError):
            raise failure_type("reply safety failure", service="x")
        raise failure_type("reply safety failure")

    def later_lane(_state: dict) -> str:
        pytest.fail("the sibling reply lane must not run after a safety failure")

    monkeypatch.setattr(
        bot,
        "maybe_reply_to_mentions",
        safety_failure if first_lane == "normal" else later_lane,
    )
    monkeypatch.setattr(
        bot,
        "maybe_reply_to_quote_tweets",
        safety_failure if first_lane == "quote_tweet" else later_lane,
    )

    assert bot.run_test_cycle() == 0
    assert waited == [expected_wait_lane]
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)


def test_test_post_meme_migrates_old_meme_schedule_before_post_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    state = {
        "meme_schedule_version": 1,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    seen_states: list[dict] = []
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "post_next_meme", lambda state: seen_states.append(json.loads(json.dumps(state))))

    assert bot.run_test_post_meme() == 0
    assert seen_states[0]["meme_schedule_version"] == bot.MEME_SCHEDULE_VERSION
    assert seen_states[0]["next_meme_schedule_mode"] == "fallback_migrated"


def test_load_state_rejects_absurd_epoch_primary_and_recovers_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    bot.atomic_write_json(
        state_file,
        {
            "next_meme_post_epoch": 10**30,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": "9999-01-01",
            "meme_anchor_quote_post_epoch": 0,
        },
    )
    bot.atomic_write_json(
        tmp_path / "bot_state.json.bak1",
        {
            "replied_to_ids": ["from-bak1"],
            "next_quote_post_epoch": 1_800_000_000,
        },
    )

    recovered = bot.load_state()

    assert recovered["replied_to_ids"] == ["from-bak1"]
    assert recovered["next_quote_post_epoch"] == 1_800_000_000


def test_load_state_refuses_valid_primary_latest_backup_divergence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two valid latest-generation candidates cannot be ordered by pathname."""

    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    bot.atomic_write_json(
        state_file,
        {
            "replied_to_ids": [],
            "last_reply_epoch": 1_800_000_000,
            "next_quote_post_epoch": 1_800_001_000,
        },
    )
    bot.atomic_write_json(
        tmp_path / "bot_state.json.bak1",
        {
            "replied_to_ids": ["950001"],
            "last_reply_epoch": 1_800_000_100,
            "next_quote_post_epoch": 1_800_002_000,
        },
    )

    with pytest.raises(
        RuntimeError,
        match="Primary state and latest committed backup.*diverge",
    ):
        bot.load_state()


def test_load_state_accepts_matching_primary_and_latest_backup_after_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    state = bot.default_state()
    state["last_reply_epoch"] = 1_800_000_100

    bot.save_state(state, durable=True)

    assert state_file.read_bytes() == (tmp_path / "bot_state.json.bak1").read_bytes()
    assert bot.load_state()["last_reply_epoch"] == 1_800_000_100


def test_load_state_accepts_semantically_equal_differently_encoded_latest_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON layout differences do not constitute state-generation divergence."""

    state_file = tmp_path / "bot_state.json"
    backup_file = tmp_path / "bot_state.json.bak1"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    state = bot.default_state()
    state["last_reply_epoch"] = 1_800_000_100
    bot.atomic_write_json(state_file, state)
    reordered = dict(reversed(list(state.items())))
    backup_file.write_text(
        json.dumps(reordered, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    backup_file.chmod(0o600)

    assert state_file.read_bytes() != backup_file.read_bytes()
    assert bot.load_state()["last_reply_epoch"] == 1_800_000_100


def test_load_state_does_not_let_stale_older_backup_veto_usable_latest_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 3)
    state = bot.default_state()
    state["last_reply_epoch"] = 1_800_000_100
    bot.save_state(state, durable=True)
    bot.atomic_write_json(
        tmp_path / "bot_state.json.bak2",
        {
            "pending_reply_drafts": {
                "mention:100": {"reply_text": "obsolete draft"},
            }
        },
    )

    assert bot.load_state()["last_reply_epoch"] == 1_800_000_100


def test_load_state_ignores_stale_backup_when_backups_are_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disabled backup generation is not part of the recovery authority."""

    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    bot.atomic_write_json(
        state_file,
        {
            "last_reply_epoch": 1_800_000_100,
            "next_quote_post_epoch": 1_800_001_000,
        },
    )
    bot.atomic_write_json(
        tmp_path / "bot_state.json.bak1",
        {
            "last_reply_epoch": 1_800_000_000,
            "next_quote_post_epoch": 1_800_000_500,
        },
    )

    recovered = bot.load_state()

    assert recovered["last_reply_epoch"] == 1_800_000_100
    assert recovered["next_quote_post_epoch"] == 1_800_001_000


def test_common_receipt_loader_requires_canonical_owned_private_file(
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_bytes(b"{}")
    receipt.chmod(0o600)
    with pytest.raises(bot.UnsafeReceiptNamespace, match="not canonical"):
        bot.load_receipt_json_no_follow(receipt)

    receipt.write_bytes(bot.canonical_atomic_json_bytes({}))
    receipt.chmod(0o644)
    with pytest.raises(
        bot.UnsafeReceiptNamespace,
        match="unsafe ownership or permissions",
    ):
        bot.load_receipt_json_no_follow(receipt)


def test_common_receipt_loader_rejects_same_inode_mutation_after_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_bytes(bot.canonical_atomic_json_bytes({}))
    receipt.chmod(0o600)
    real_lstat = bot.os.lstat
    calls = {"count": 0}

    def mutate_before_final_path_snapshot(path: os.PathLike[str] | str):
        calls["count"] += 1
        if calls["count"] == 2:
            receipt.write_bytes(
                bot.canonical_atomic_json_bytes({"changed": True})
            )
        return real_lstat(path)

    monkeypatch.setattr(bot.os, "lstat", mutate_before_final_path_snapshot)

    with pytest.raises(bot.UnsafeReceiptNamespace, match="changed while reading"):
        bot.load_receipt_json_no_follow(receipt)


def test_current_main_attempt_requires_exact_string_identifiers() -> None:
    attempt = bot.build_main_post_attempt(
        lane="daily_meme",
        text="A meme post.",
        media_ids=["700001"],
        made_with_ai=False,
        selected_identity={"meme_basename": "001_meme.png"},
        recovery_plan={
            "next_schedule_mode": "fallback",
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "fallback_hour": 13,
            "fallback_minute": 0,
            "image_summary": "A meme image.",
            "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
        },
        attempt_epoch=1_800_000_000,
    )
    assert bot.main_post_attempt_is_semantically_valid(attempt)

    changed = copy.deepcopy(attempt)
    changed["attempt_id"] = int("1" * 64)
    assert bot.main_post_attempt_is_semantically_valid(changed) is False

    changed = copy.deepcopy(attempt)
    changed["reply_to_id"] = 0
    assert bot.main_post_attempt_is_semantically_valid(changed) is False

    changed = copy.deepcopy(attempt)
    changed["selected_identity"]["meme_basename"] = 123
    assert bot.main_post_attempt_is_semantically_valid(changed) is False

    attempting = {**attempt, "lifecycle_state": "attempting"}
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempting,
        post_id="950001",
        confirmation_epoch=1_800_000_001,
        image_summary="A meme image.",
    )
    pending["post_id"] = 950001
    assert (
        bot.confirmed_pending_schedule_receipt_is_semantically_valid(pending)
        is False
    )


def test_current_conversational_receipt_requires_string_identifier_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "ai_reply_receipt_draft_is_valid", lambda *_: True)
    receipt = {
        "schema_version": 4,
        "lifecycle_state": "sending",
        "target_id": "111",
        "author_id": "222",
        "candidate_source": "mention",
        "conversation_id": "111",
        "reply_text": "A reviewed reply.",
        "reply_context": {
            "target_id": "111",
            "thread_id": "111",
            "lane": "mention",
        },
        "ai_reply_draft": {},
        "attempt_epoch": 1_800_000_000,
        "reply_epoch": 1_800_000_000,
        "daily_reply_date": bot.epoch_date_str(1_800_000_000),
    }
    assert bot.sending_reply_receipt_is_semantically_valid(receipt)

    for field in ("target_id", "author_id", "conversation_id"):
        changed = copy.deepcopy(receipt)
        changed[field] = 111
        if field == "target_id":
            changed["reply_context"]["target_id"] = 111
        if field == "conversation_id":
            changed["reply_context"]["thread_id"] = 111
        assert bot.sending_reply_receipt_is_semantically_valid(changed) is False

    confirmed = bot._confirmed_reply_receipt_from_sending(
        receipt,
        reply_post_id="950002",
        confirmation_epoch=1_800_000_001,
    )
    confirmed["reply_post_id"] = 950002
    assert bot.confirmed_reply_receipt_is_semantically_valid(confirmed) is False


def test_load_state_rejects_malformed_tweet_cache_epoch_and_recovers_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    bot.atomic_write_json(
        state_file,
        {"tweet_cache": {"123": {"cached_epoch": "banana"}}},
    )
    bot.atomic_write_json(
        tmp_path / "bot_state.json.bak1",
        {"replied_to_ids": ["from-bak1"], "tweet_cache": {}},
    )

    recovered = bot.load_state()

    assert recovered["replied_to_ids"] == ["from-bak1"]
    assert recovered["tweet_cache"] == {}


@pytest.mark.parametrize(
    "cache_entry",
    [
        {"referenced_tweets": "banana"},
        {"referenced_tweets": ["banana"]},
    ],
)
def test_load_state_rejects_malformed_tweet_cache_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cache_entry: dict,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    bot.atomic_write_json(state_file, {"tweet_cache": {"123": cache_entry}})
    bot.atomic_write_json(tmp_path / "bot_state.json.bak1", {"tweet_cache": {"456": {"cached_epoch": 1_800_000_000}}})

    recovered = bot.load_state()

    assert "123" not in recovered["tweet_cache"]
    assert recovered["tweet_cache"]["456"]["cached_epoch"] == 1_800_000_000


def test_tweet_cache_normalisation_stringifies_scalars_and_preserves_valid_context(tmp_path: Path) -> None:
    state = {
        "tweet_cache": {
            123: {
                "id": 123,
                "author_id": 456,
                "conversation_id": 789,
                "text": 12345,
                "image_summary": 67890,
                "created_at": 111,
                "post_type": 222,
                "cached_epoch": "1800000000",
                "referenced_tweets": [{"type": 333, "id": 444, "extra": 555}],
            }
        }
    }

    normalised = bot.normalise_state_candidate(state, path=tmp_path / "bot_state.json")

    assert normalised is not None
    entry = normalised["tweet_cache"]["123"]
    assert entry["id"] == "123"
    assert entry["author_id"] == "456"
    assert entry["conversation_id"] == "789"
    assert entry["text"] == "12345"
    assert entry["image_summary"] == "67890"
    assert entry["created_at"] == "111"
    assert entry["post_type"] == "222"
    assert entry["cached_epoch"] == 1_800_000_000
    assert entry["referenced_tweets"] == [{"type": "333", "id": "444", "extra": "555"}]


def test_valid_tweet_cache_round_trips_through_state_loading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    bot.atomic_write_json(
        state_file,
        {
            "tweet_cache": {
                "123": {
                    "id": "123",
                    "author_id": "456",
                    "conversation_id": "789",
                    "text": "Useful cached context",
                    "image_summary": "A useful image summary",
                    "created_at": "2026-07-06T10:00:00",
                    "post_type": "quote",
                    "cached_epoch": 1_800_000_000,
                    "referenced_tweets": [{"type": "replied_to", "id": "111"}],
                }
            }
        },
    )

    recovered = bot.load_state()

    assert recovered["tweet_cache"]["123"] == {
        "id": "123",
        "author_id": "456",
        "conversation_id": "789",
        "text": "Useful cached context",
        "image_summary": "A useful image summary",
        "created_at": "2026-07-06T10:00:00",
        "post_type": "quote",
        "cached_epoch": 1_800_000_000,
        "referenced_tweets": [{"type": "replied_to", "id": "111"}],
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("false", False),
        ("1", True),
        ("0", False),
        ("yes", True),
        ("no", False),
        ("on", True),
        ("off", False),
        ("banana", False),
        ([], False),
        ({}, False),
        (None, False),
        ("", False),
    ],
)
def test_control_bool_parses_explicit_values(value: object, expected: bool) -> None:
    assert bot.control_bool({"disable_meme_posts": value}, "disable_meme_posts") is expected


def test_control_bool_missing_value_is_false() -> None:
    assert bot.control_bool({}, "disable_meme_posts") is False


@pytest.mark.parametrize("post_id", ["123456", 123456])
def test_create_post_accepts_valid_numeric_ids(monkeypatch: pytest.MonkeyPatch, post_id: object) -> None:
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *args, **kwargs: {"data": {"id": post_id}},
    )
    receipt = prepare_unit_historical_context_create(text="hello")

    assert bot.create_post(
        "hello",
        reply_to_id=str(receipt["parent_post_id"]),
        prepared_historical_context_reply_receipt=receipt,
    ) == {"data": {"id": post_id}}


def test_create_post_passes_long_text_without_280_character_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Historically grounded context. " * 20
    assert len(text) > 280
    requests = []
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *args, **kwargs: requests.append((args, kwargs))
        or {"data": {"id": "123456"}},
    )
    receipt = prepare_unit_historical_context_create(
        text=text,
        parent_post_id="654321",
    )

    bot.create_post(
        text,
        reply_to_id="654321",
        prepared_historical_context_reply_receipt=receipt,
    )

    assert requests[0][0] == ("POST", "/2/tweets")
    assert requests[0][1]["json"]["text"] == text
    assert requests[0][1]["json"]["reply"] == {"in_reply_to_tweet_id": "654321"}


@pytest.mark.parametrize(
    "response",
    [
        {"data": {"id": "banana"}},
        {"data": {"id": ""}},
        {"data": {}},
        {"data": []},
        {"data": "not an object"},
        {},
    ],
)
def test_create_post_rejects_invalid_or_missing_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: dict,
) -> None:
    marker = tmp_path / "ambiguous_post.json"
    monkeypatch.setattr(bot, "AMBIGUOUS_POST_OUTCOME_FILE", marker)
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *args, **kwargs: response,
    )
    receipt = prepare_unit_historical_context_create(text="hello")

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.create_post(
            "hello",
            reply_to_id=str(receipt["parent_post_id"]),
            prepared_historical_context_reply_receipt=receipt,
        )

    assert json.loads(marker.read_text(encoding="utf-8"))["outcome"] == "ambiguous_remote_post"


def test_malformed_reply_post_id_is_not_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0

    mention = {
        "id": "100",
        "author_id": "200",
        "text": "@MrsMThatcher hello",
        "conversation_id": "100",
        "referenced_tweets": [],
    }

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
    monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda state: [mention])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda text: False)
    context = unit_reply_context(target_id="100", contribution="@MrsMThatcher hello")
    monkeypatch.setattr(bot, "build_context_for_reply_ai", lambda mention, state: (context, True))
    monkeypatch.setattr(
        bot,
        "generate_ai_first_reply",
        lambda actual_context, *_args, **_kwargs: unit_approved_reply(actual_context),
    )
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *args, **kwargs: {"data": {"id": "banana"}},
    )
    monkeypatch.setattr(bot, "save_state", lambda state, **_kwargs: None)

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.maybe_reply_to_mentions(state)

    assert bot.ambiguous_remote_post_is_blocking() is True
    receipt_status, receipt = bot.load_confirmed_reply_receipt()
    assert receipt_status == "sending"
    assert receipt is not None
    assert receipt["target_id"] == "100"
    assert "reply_post_id" not in receipt
    assert state["daily_reply_count"] == 0
    assert state["replied_to_ids"] == []
    assert state["own_auto_reply_ids"] == []
    assert state["tweet_cache"] == {}


@pytest.mark.parametrize("candidate_source", ["mention", "hot_post_reply"])
def test_own_historical_context_reply_is_never_processed_as_incoming_reply(
    monkeypatch: pytest.MonkeyPatch,
    candidate_source: str,
) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0
    own_context_reply = {
        "id": "123456",
        "author_id": str(bot.MY_USER_ID),
        "text": "Context\nSpoken during: A speech.\n\nVerification: Exact wording",
        "conversation_id": "654321",
        "referenced_tweets": [{"type": "replied_to", "id": "654321"}],
        "_source": candidate_source,
        "_hot_original_post_id": "654321",
    }

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda state: [own_context_reply] if candidate_source == "mention" else [])
    monkeypatch.setattr(
        bot,
        "get_hot_post_reply_candidates",
        lambda state: [own_context_reply] if candidate_source == "hot_post_reply" else [],
    )
    monkeypatch.setattr(
        bot,
        "build_context_for_reply_ai",
        lambda *args, **kwargs: pytest.fail("own context reply must not be sent to xAI"),
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda *args, **kwargs: pytest.fail("own context reply must not receive another X reply"),
    )
    monkeypatch.setattr(bot, "save_state", lambda *args, **kwargs: None)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert state["daily_reply_count"] == 0
    assert state["replied_to_ids"] == []


def test_truncated_pagination_no_reply_is_not_evaluated_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0
    mention = {
        "id": "100",
        "author_id": "200",
        "text": "@MrsMThatcher Yep",
        "conversation_id": "100",
        "referenced_tweets": [],
        "_pagination_truncated": True,
    }
    calls: list[str] = []

    def no_reply(_context: dict, *_args: object, evaluation_outcome=None, **_kwargs: object):
        calls.append("xai")
        evaluation_outcome.update({"status": "no_reply", "reason": "no useful response"})
        return None

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: [dict(mention)])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    context = unit_reply_context(target_id="100", contribution=mention["text"])
    monkeypatch.setattr(bot, "build_context_for_reply_ai", lambda *_args: (context, True))
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(bot, "generate_ai_first_reply", no_reply)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED

    assert calls == ["xai"]
    assert state["reply_evaluation_records"]["100"]["outcome"] == "no_reply"


def test_truncated_pagination_rejected_model_output_is_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    mention = {
        "id": "101",
        "author_id": "201",
        "text": "@MrsMThatcher A substantive claim",
        "conversation_id": "101",
        "referenced_tweets": [],
        "_pagination_truncated": True,
    }
    calls: list[str] = []

    def rejected(_context: dict, *_args: object, evaluation_outcome=None, **_kwargs: object):
        calls.append("xai")
        evaluation_outcome.update({"status": "no_reply", "reason": "reviewer_rejected"})
        return None

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: [dict(mention)])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    context = unit_reply_context(target_id="101", contribution=mention["text"])
    monkeypatch.setattr(bot, "build_context_for_reply_ai", lambda *_args: (context, True))
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(bot, "generate_ai_first_reply", rejected)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED

    assert calls == ["xai"]
    assert state["reply_evaluation_records"]["101"]["reason"] == "reviewer_rejected"


def test_confirmed_mention_reply_save_failure_replays_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(SCENARIOS / "normal_mention_reply.json")
    scenario["enable_pagination"] = True
    scenario["mentions"] = [
        {
            "id": str(tweet_id),
            "author_id": str(400 + tweet_id),
            "conversation_id": str(tweet_id),
            "text": "@a @b @MrsMThatcher",
        }
        for tweet_id in (204, 203, 202, 201)
    ]
    scenario["mentions"].extend(
        [
            {
                "id": "200",
                "author_id": "600",
                "conversation_id": "200",
                "text": "@MrsMThatcher Good sense still matters.",
            },
            {
                "id": "150",
                "author_id": "550",
                "conversation_id": "150",
                "text": "@a @b @MrsMThatcher",
            },
        ]
    )
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        state_file = tmp_path / "bot_state.json"
        control_file = tmp_path / "mrsMThatcher.control.json"
        watch_file = tmp_path / "extra_quote_watch_post_ids.txt"

        monkeypatch.setattr(bot, "STATE_FILE", state_file)
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", control_file)
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", watch_file)
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
        monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MAX_MENTIONS_PER_CHECK", 5)
        monkeypatch.setattr(bot, "MENTIONS_MAX_PAGES_PER_CHECK", 1)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
        monkeypatch.setattr(
            bot,
            "generate_ai_first_reply",
            lambda context, *_args, **_kwargs: unit_approved_reply(
                context,
                text="Quite right. Good sense still matters.",
                mode="opinion_or_principle",
            ),
        )

        original_save_state = bot.save_state
        initial_state = bot.default_state()
        initial_state.update(
            {
                "last_seen_mention_id": "99",
                "daily_reply_date": bot.current_datetime().strftime("%Y-%m-%d"),
                "daily_reply_count": 0,
                "last_reply_epoch": 0,
                "replied_to_ids": [],
                "daily_replied_author_ids": [],
                "daily_replied_author_counts": {},
                "own_auto_reply_ids": [],
                "tweet_cache": {},
            }
        )
        original_save_state(initial_state)

        def fail_first_post_success_save(state: dict, **kwargs: object) -> None:
            if server.posts:
                raise OSError("injected post-success save failure")
            original_save_state(state, **kwargs)

        monkeypatch.setattr(bot, "save_state", fail_first_post_success_save)
        first_state = bot.load_state()

        with pytest.raises(OSError, match="injected post-success save failure"):
            bot.maybe_reply_to_mentions(first_state)

        assert len(server.posts) == 1
        first_reply_id = "900000"
        assert server.posts[0]["reply"]["in_reply_to_tweet_id"] == "200"

        durable_after_failed_save = json.loads(state_file.read_text(encoding="utf-8"))
        assert "200" not in durable_after_failed_save.get("replied_to_ids", [])
        assert durable_after_failed_save.get("last_seen_mention_id") == "99"
        assert durable_after_failed_save.get("mention_pagination") == {
            "base_since_id": "99",
            "next_token": "5",
        }
        assert durable_after_failed_save.get("daily_reply_count") == 0
        assert durable_after_failed_save.get("last_reply_epoch") == 0
        assert durable_after_failed_save.get("daily_replied_author_counts", {}) == {}
        assert first_reply_id not in durable_after_failed_save.get("own_auto_reply_ids", [])
        receipt_status, receipt = bot.load_confirmed_reply_receipt()
        assert receipt_status == "valid"
        assert receipt is not None
        assert receipt["mention_pagination"] == {
            "base_since_id": "99",
            "next_token": "5",
        }

        monkeypatch.setattr(bot, "save_state", original_save_state)
        restarted_state = bot.load_state()
        assert "200" not in restarted_state.get("replied_to_ids", [])
        assert restarted_state.get("last_seen_mention_id") == "99"
        assert restarted_state["mention_pagination"] == {
            "base_since_id": "99",
            "next_token": "5",
        }

        second_status = bot.maybe_reply_to_mentions(restarted_state)

        assert second_status == bot.NORMAL_CHECK_STATUS_CHECKED
        assert [post["reply"]["in_reply_to_tweet_id"] for post in server.posts] == ["200"]
        mention_requests = [
            request
            for request in server.requests
            if request["path"].endswith("/mentions")
        ]
        assert mention_requests[1]["query"]["since_id"] == ["99"]
        assert mention_requests[1]["query"]["pagination_token"] == ["5"]
        assert restarted_state["mention_pagination"] == {}
    finally:
        server.stop()


def test_mention_native_photo_context_reaches_xai(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = {
        "mentions": [
            {
                "id": "100",
                "text": "You've been conquered. https://t.co/example",
                "author_id": "200",
                "conversation_id": "100",
                "created_at": "2026-07-06T10:00:00Z",
                "attachments": {"media_keys": ["3_100"]},
            }
        ],
        "mentions_extra": {
            "includes": {
                "media": [
                    {
                        "media_key": "3_100",
                        "type": "photo",
                        "url": "https://pbs.twimg.com/media/native-photo.jpg",
                    }
                ]
            }
        },
        "grok_replies": ["SKIP"],
    }
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")

        candidates = bot.get_mentions(state)
        media = bot.reply_media_context_for_candidate(candidates[0], lane="mention", target_id="100")
        user_content = bot.xai_user_content("You've been conquered.", media)
        assert isinstance(user_content, list)
        text_parts = [part["text"] for part in user_content if part.get("type") == "text"]
        image_parts = [part for part in user_content if part.get("type") == "image_url"]
        assert text_parts
        assert "You've been conquered." in text_parts[0]
        assert "https://t.co/example" not in text_parts[0]
        assert image_parts == [
            {
                "type": "image_url",
                "image_url": {"url": "https://pbs.twimg.com/media/native-photo.jpg"},
            }
        ]
    finally:
        server.stop()


def test_text_only_mention_keeps_plain_xai_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = {
        "mentions": [
            {
                "id": "100",
                "text": "A plain comment.",
                "author_id": "200",
                "conversation_id": "100",
                "created_at": "2026-07-06T10:00:00Z",
            }
        ],
        "grok_replies": ["SKIP"],
    }
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")

        candidates = bot.get_mentions(state)
        media = bot.reply_media_context_for_candidate(candidates[0], lane="mention", target_id="100")
        assert isinstance(bot.xai_user_content("A plain comment.", media), str)
        assert media["status"] == "none"
    finally:
        server.stop()


def test_mention_external_url_without_native_photo_is_not_image_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = {
        "mentions": [
            {
                "id": "100",
                "text": "Look at this https://example.com/story",
                "author_id": "200",
                "conversation_id": "100",
                "created_at": "2026-07-06T10:00:00Z",
            }
        ],
        "grok_replies": ["SKIP"],
    }
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")

        candidates = bot.get_mentions(state)
        media = bot.reply_media_context_for_candidate(candidates[0], lane="mention", target_id="100")
        content = bot.xai_user_content("Look at this", media)
        assert isinstance(content, str)
        assert media["status"] == "none"
    finally:
        server.stop()


def test_mention_native_photo_context_caps_multiple_photos_in_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = {
        "mentions": [
            {
                "id": "100",
                "text": "Several images attached.",
                "author_id": "200",
                "conversation_id": "100",
                "created_at": "2026-07-06T10:00:00Z",
                "attachments": {"media_keys": ["3_a", "3_b", "3_c"]},
            }
        ],
        "mentions_extra": {
            "includes": {
                "media": [
                    {"media_key": "3_a", "type": "photo", "url": "https://pbs.twimg.com/media/a.jpg"},
                    {"media_key": "3_b", "type": "photo", "url": "https://pbs.twimg.com/media/b.jpg"},
                    {"media_key": "3_c", "type": "photo", "url": "https://pbs.twimg.com/media/c.jpg"},
                ]
            }
        },
        "grok_replies": ["SKIP"],
    }
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")

        candidates = bot.get_mentions(state)
        media = bot.reply_media_context_for_candidate(candidates[0], lane="mention", target_id="100")
        content = bot.xai_user_content("Several images attached.", media)
        assert isinstance(content, list)
        assert [part["image_url"]["url"] for part in content if part.get("type") == "image_url"] == [
            "https://pbs.twimg.com/media/a.jpg",
            "https://pbs.twimg.com/media/b.jpg",
        ]
    finally:
        server.stop()


def test_native_photo_unavailable_adds_incomplete_context_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = {
        "mentions": [
            {
                "id": "100",
                "text": "This only makes sense with the image. https://t.co/example",
                "author_id": "200",
                "conversation_id": "100",
                "created_at": "2026-07-06T10:00:00Z",
                "attachments": {"media_keys": ["3_100"]},
            }
        ],
        "mentions_extra": {
            "includes": {
                "media": [
                    {
                        "media_key": "3_100",
                        "type": "photo",
                    }
                ]
            }
        },
        "grok_replies": ["SKIP"],
    }
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")

        candidates = bot.get_mentions(state)
        media = bot.reply_media_context_for_candidate(candidates[0], lane="mention", target_id="100")
        content = bot.xai_user_content("This only makes sense with the image.", media)
        assert isinstance(content, str)
        assert "could not be made available" in content
        assert "Do not invent image contents" in content
        assert "https://t.co/example" not in content
    finally:
        server.stop()


def test_multimodal_xai_rejection_fails_closed_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = {
        "mentions": [
            {
                "id": "100",
                "text": "This depends on the image. https://t.co/example",
                "author_id": "200",
                "conversation_id": "100",
                "created_at": "2026-07-06T10:00:00Z",
                "attachments": {"media_keys": ["3_100"]},
            }
        ],
        "mentions_extra": {
            "includes": {
                "media": [
                    {
                        "media_key": "3_100",
                        "type": "photo",
                        "url": "https://pbs.twimg.com/media/native-photo.jpg",
                    }
                ]
            }
        },
        "xai_responses": [
            {"status": 400, "body": {"error": "image input rejected"}},
            {
                "status": 200,
                "body": {
                    "choices": [{"message": {"content": "SKIP"}}],
                    "usage": {"total_tokens": 12},
                },
            },
        ],
    }
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")

        media = {
            "status": "supplied",
            "photos": [{"url": "https://pbs.twimg.com/media/native-photo.jpg"}],
        }
        with pytest.raises(bot.ApiError, match="error 400"):
            bot.xai_structured_reply_call(
                stage="proposer",
                model="unit-model",
                system_prompt="system",
                user_prompt="user",
                response_schema={"type": "object"},
                timeout_seconds=5,
                max_output_tokens=100,
                media_context=media,
            )

        xai_posts = [request for request in server.requests if request["path"] == "/v1/chat/completions"]
        assert len(xai_posts) == 1
        assert isinstance(xai_posts[0]["body"]["messages"][1]["content"], list)
    finally:
        server.stop()


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (400, {"error": "invalid request body"}),
        (403, {"error": "forbidden"}),
        (415, {"error": "unsupported content type application/json"}),
        (422, {"error": "unsupported input type"}),
        (422, {"error": "invalid provision"}),
        (422, {"error": "invalid revision"}),
        (422, {"error": "invalid supervision setting"}),
        (
            400,
            {
                "error": {"message": "invalid request body"},
                "request_fragment": {"type": "image_url"},
            },
        ),
        (401, {"error": "configured 401 failure"}),
        (429, {"error": "configured 429 failure"}),
        (503, {"error": "configured 503 failure"}),
    ],
)
def test_xai_multimodal_rejection_classifier_rejects_unrelated_errors(
    status_code: int,
    body: dict,
) -> None:
    assert bot.xai_error_is_multimodal_input_rejection(fake_xai_response(status_code, body)) is False


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (400, {"error": "unsupported image_url in multimodal input"}),
        (415, {"error": "image content type is unsupported"}),
        (422, {"error": "invalid multimodal image input"}),
        (422, {"error": "invalid vision input"}),
        (
            400,
            {
                "error": {"message": "unsupported image_url in multimodal input"},
                "request_fragment": {"type": "image_url"},
            },
        ),
    ],
)
def test_xai_multimodal_rejection_classifier_accepts_media_specific_errors(
    status_code: int,
    body: dict,
) -> None:
    assert bot.xai_error_is_multimodal_input_rejection(fake_xai_response(status_code, body)) is True


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (400, {"error": "invalid request body"}),
        (403, {"error": "forbidden"}),
        (415, {"error": "unsupported content type application/json"}),
        (422, {"error": "unsupported input type"}),
        (
            400,
            {
                "error": {"message": "invalid request body"},
                "request_fragment": {"type": "image_url"},
            },
        ),
        (401, {"error": "configured 401 failure"}),
        (429, {"error": "configured 429 failure"}),
        (503, {"error": "configured 503 failure"}),
    ],
)
def test_non_image_xai_http_errors_do_not_retry_as_text_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    body: dict,
) -> None:
    status, xai_posts, successful_xai_requests, _state = run_native_photo_mention_with_xai_responses(
        tmp_path,
        monkeypatch,
        [
            {"status": status_code, "body": body},
            {
                "status": 200,
                "body": {
                    "choices": [{"message": {"content": "SKIP"}}],
                    "usage": {"total_tokens": 12},
                },
            },
        ],
    )

    assert status == bot.NORMAL_CHECK_STATUS_API_ERROR
    assert len(xai_posts) == 1
    assert isinstance(xai_posts[0]["body"]["messages"][1]["content"], list)
    assert successful_xai_requests == []


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (400, {"error": "unsupported image_url in multimodal input"}),
        (415, {"error": "image content type is unsupported"}),
        (422, {"error": "invalid multimodal image input"}),
    ],
)
def test_image_xai_http_rejection_fails_closed_without_text_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    body: dict,
) -> None:
    status, xai_posts, successful_xai_requests, _state = run_native_photo_mention_with_xai_responses(
        tmp_path,
        monkeypatch,
        [
            {"status": status_code, "body": body},
            {
                "status": 200,
                "body": {
                    "choices": [{"message": {"content": "SKIP"}}],
                    "usage": {"total_tokens": 12},
                },
            },
            {
                "status": 200,
                "body": {
                    "choices": [{"message": {"content": "This third response must not be used."}}],
                    "usage": {"total_tokens": 99},
                },
            },
        ],
    )

    assert status == bot.NORMAL_CHECK_STATUS_API_ERROR
    assert len(xai_posts) == 1
    assert isinstance(xai_posts[0]["body"]["messages"][1]["content"], list)
    assert successful_xai_requests == []


def test_quote_tweet_native_photo_context_reaches_xai(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    scenario["quote_tweets"]["900"]["data"][0]["attachments"] = {"media_keys": ["3_910"]}
    scenario["quote_tweets"]["900"]["includes"]["media"] = [
        {
            "media_key": "3_910",
            "type": "photo",
            "url": "https://pbs.twimg.com/media/quote-photo.jpg",
        }
    ]
    scenario["grok_replies"] = ["SKIP"]
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_QUOTE_REPLIES_PER_DAY", 10)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "QUOTE_REPLY_DELAY_SECONDS", 0)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        state = bot.default_state()
        state["recent_own_post_ids"] = ["900"]
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")
        state["daily_quote_reply_date"] = state["daily_reply_date"]

        candidates = scenario["quote_tweets"]["900"]["data"]
        bot.attach_media_to_tweets(candidates, scenario["quote_tweets"]["900"]["includes"])
        media = bot.reply_media_context_for_candidate(candidates[0], lane="quote_tweet", target_id="910")
        assert media["photos"] == [{
            "media_key": "3_910",
            "url": "https://pbs.twimg.com/media/quote-photo.jpg",
        }]
    finally:
        server.stop()


def test_strategy_persistence_failure_blocks_quote_tweet_x_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
        monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_QUOTE_REPLIES_PER_DAY", 10)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "QUOTE_REPLY_DELAY_SECONDS", 0)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
        monkeypatch.setattr(
            bot,
            "generate_ai_first_reply",
            lambda context, *_args, **_kwargs: unit_approved_reply(
                context,
                text="Conviction matters more than applause.",
                mode="opinion_or_principle",
            ),
        )
        monkeypatch.setattr(bot, "store_pending_ai_reply", lambda *_args, **_kwargs: False)
        monkeypatch.setattr(
            bot,
            "create_post",
            lambda *_args, **_kwargs: pytest.fail("X write must not be called"),
        )
        monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

        state = bot.default_state()
        state["recent_own_post_ids"] = ["900"]
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")
        state["daily_quote_reply_date"] = state["daily_reply_date"]

        assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
        assert state["daily_reply_count"] == 0
        assert "910" in state["seen_quote_post_ids"]
    finally:
        server.stop()


def test_quote_tweet_model_no_reply_is_durable_beyond_bounded_scan_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    state = bot.default_state()
    state["recent_own_post_ids"] = ["900"]
    state["daily_reply_date"] = datetime.fromtimestamp(fixed_epoch).strftime("%Y-%m-%d")
    state["daily_quote_reply_date"] = state["daily_reply_date"]
    own_post = {
        "id": "900", "author_id": "12345", "text": "An original post.",
        "conversation_id": "900", "referenced_tweets": [],
    }
    quote_post = {
        "id": "910", "author_id": "777", "text": "A substantive but unproductive claim.",
        "conversation_id": "910",
        "referenced_tweets": [{"type": "quoted", "id": "900"}],
    }
    model_calls: list[str] = []

    def no_reply(*_args: object, **kwargs: object) -> None:
        model_calls.append("called")
        outcome = kwargs.get("evaluation_outcome")
        if isinstance(outcome, dict):
            outcome.update({"status": "no_reply", "reason": "no_reply_due_to_unverifiable_claim"})
        return None

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
    monkeypatch.setattr(bot, "MAX_QUOTE_REPLIES_PER_DAY", 10)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "reconcile_confirmed_reply_receipt", lambda _state: False)
    monkeypatch.setattr(bot, "build_quote_lookup_post_ids", lambda _state: ["900"])
    monkeypatch.setattr(bot, "get_tweet_by_id_cached", lambda *_args, **_kwargs: dict(own_post))
    monkeypatch.setattr(bot, "get_quote_tweets_for_post", lambda *_args, **_kwargs: [dict(quote_post)])
    monkeypatch.setattr(bot, "quote_tweet_is_old_enough", lambda _tweet: True)
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(bot, "generate_ai_first_reply", no_reply)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
    state["seen_quote_post_ids"] = []
    state["skipped_quote_post_ids"] = []
    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED

    assert model_calls == ["called"]
    assert state["reply_evaluation_records"]["910"]["outcome"] == "no_reply"


def test_quote_tweet_generic_403_remains_ambiguous_and_durable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    state = bot.default_state()
    state["recent_own_post_ids"] = ["900"]
    state["daily_reply_date"] = datetime.fromtimestamp(fixed_epoch).strftime("%Y-%m-%d")
    state["daily_quote_reply_date"] = state["daily_reply_date"]
    own_post = {
        "id": "900", "author_id": "12345", "text": "An original post.",
        "conversation_id": "900", "referenced_tweets": [],
    }
    quote_post = {
        "id": "910", "author_id": "777", "text": "A substantive comment.",
        "conversation_id": "910",
        "referenced_tweets": [{"type": "quoted", "id": "900"}],
    }
    model_calls: list[str] = []
    post_calls: list[str] = []

    def reply(context: dict, *_args: object, **_kwargs: object) -> AIReply:
        model_calls.append("called")
        return unit_approved_reply(
            context,
            text="Conviction still matters.",
            mode="opinion_or_principle",
        )

    def forbidden_post(*_args: object, **_kwargs: object) -> dict:
        post_calls.append("called")
        raise bot.ApiError(
            "You can only reply to or quote posts where you are mentioned or are the author",
            service="x",
            status_code=403,
        )

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
    monkeypatch.setattr(bot, "MAX_QUOTE_REPLIES_PER_DAY", 10)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "reconcile_confirmed_reply_receipt", lambda _state: False)
    monkeypatch.setattr(bot, "build_quote_lookup_post_ids", lambda _state: ["900"])
    monkeypatch.setattr(bot, "get_tweet_by_id_cached", lambda *_args, **_kwargs: dict(own_post))
    monkeypatch.setattr(bot, "get_quote_tweets_for_post", lambda *_args, **_kwargs: [dict(quote_post)])
    monkeypatch.setattr(bot, "quote_tweet_is_old_enough", lambda _tweet: True)
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(bot, "generate_ai_first_reply", reply)
    monkeypatch.setattr(bot, "create_post", forbidden_post)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.maybe_reply_to_quote_tweets(state)

    assert model_calls == ["called"]
    assert post_calls == ["called"]
    status, receipt = bot.load_confirmed_reply_receipt()
    assert status == "sending"
    assert receipt is not None
    assert receipt["target_id"] == "910"
    assert state.get("reply_evaluation_records", {}).get("910", {}).get("outcome") != (
        "reply_not_permitted"
    )


def test_hot_post_reply_native_photo_context_reaches_xai(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = {
        "tweets": {
            "900": {
                "id": "900",
                "text": "Original watched post.",
                "author_id": "12345",
                "conversation_id": "900",
                "created_at": "2026-07-06T09:00:00Z",
            }
        },
        "search_recent": [
            {
                "id": "910",
                "text": "A hot reply with an image. https://t.co/example",
                "author_id": "310",
                "conversation_id": "900",
                "created_at": "2026-07-06T10:00:00Z",
                "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
                "referenced_tweets": [{"type": "replied_to", "id": "900"}],
                "attachments": {"media_keys": ["3_910"]},
            }
        ],
        "search_recent_extra": {
            "includes": {
                "media": [
                    {
                        "media_key": "3_910",
                        "type": "photo",
                        "url": "https://pbs.twimg.com/media/hot-photo.jpg",
                    }
                ]
            }
        },
        "grok_replies": ["SKIP"],
    }
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        watch_file = tmp_path / "extra_quote_watch_post_ids.txt"
        watch_file.write_text("900\n", encoding="utf-8")
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", watch_file)
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", True)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")

        candidates = scenario["search_recent"]
        bot.attach_media_to_tweets(candidates, scenario["search_recent_extra"]["includes"])
        media = bot.reply_media_context_for_candidate(candidates[0], lane="hot_post_reply", target_id="910")
        assert media["photos"] == [{
            "media_key": "3_910",
            "url": "https://pbs.twimg.com/media/hot-photo.jpg",
        }]
    finally:
        server.stop()


def test_confirmed_reply_receipt_reconciliation_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

    state = bot.default_state()
    state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")
    state["last_seen_mention_id"] = "99"
    receipt = unit_confirmed_reply_receipt(
        target_id="100",
        reply_post_id="900000",
        author_id="200",
        epoch=fixed_epoch,
    )

    bot.write_confirmed_reply_receipt(receipt)
    assert bot.reconcile_confirmed_reply_receipt(state) is True
    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()

    bot.write_confirmed_reply_receipt(receipt)
    assert bot.reconcile_confirmed_reply_receipt(state) is True

    assert state["daily_reply_count"] == 1
    assert state["daily_replied_author_counts"] == {"200": 1}
    assert state["replied_to_ids"].count("100") == 1
    assert state["own_auto_reply_ids"].count("900000") == 1
    assert state["last_seen_mention_id"] == "100"


def test_conversational_reply_receipt_schema_v3_lifecycle_is_explicit() -> None:
    legacy = unit_confirmed_reply_receipt()
    sending = unit_sending_reply_receipt()
    confirmed = {
        **sending,
        "lifecycle_state": "confirmed",
        "reply_post_id": "999",
    }

    assert bot.confirmed_reply_receipt_is_semantically_valid(legacy) is True
    assert bot.sending_reply_receipt_is_semantically_valid(legacy) is False
    assert bot.sending_reply_receipt_is_semantically_valid(sending) is True
    assert bot.confirmed_reply_receipt_is_semantically_valid(sending) is False
    assert bot.confirmed_reply_receipt_is_semantically_valid(confirmed) is True
    assert bot.sending_reply_receipt_is_semantically_valid(confirmed) is False


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_conversational_reply_receipt_schema_v4_separates_attempt_and_confirmation(
    lane: str,
) -> None:
    attempt_epoch = int(datetime(2026, 7, 6, 23, 59, 50).timestamp())
    confirmation_epoch = int(datetime(2026, 7, 7, 0, 0, 5).timestamp())
    sending = unit_sending_v4_reply_receipt(
        lane=lane,
        attempt_epoch=attempt_epoch,
    )
    confirmed = unit_confirmed_v4_reply_receipt(
        lane=lane,
        attempt_epoch=attempt_epoch,
        confirmation_epoch=confirmation_epoch,
    )

    assert bot.sending_reply_receipt_is_semantically_valid(sending) is True
    assert "confirmation_epoch" not in sending
    assert sending["reply_epoch"] == attempt_epoch
    assert sending["daily_reply_date"] == bot.epoch_date_str(attempt_epoch)
    assert bot.confirmed_reply_receipt_is_semantically_valid(confirmed) is True
    assert confirmed["attempt_epoch"] == attempt_epoch
    assert confirmed["confirmation_epoch"] == confirmation_epoch
    assert confirmed["reply_epoch"] == confirmation_epoch
    assert confirmed["daily_reply_date"] == bot.epoch_date_str(
        confirmation_epoch
    )
    if lane == "quote_tweet":
        assert confirmed["daily_quote_reply_date"] == bot.epoch_date_str(
            confirmation_epoch
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attempt_epoch", 2_000_000_010),
        ("confirmation_epoch", 1_999_999_999),
        ("reply_epoch", 2_000_000_004),
        ("daily_reply_date", "2033-05-19"),
    ],
)
def test_schema_v4_confirmed_receipt_rejects_inconsistent_timing(
    field: str,
    value: object,
) -> None:
    confirmed = unit_confirmed_v4_reply_receipt()
    confirmed[field] = value

    assert bot.confirmed_reply_receipt_is_semantically_valid(confirmed) is False


def test_schema_v4_sending_receipt_rejects_invented_confirmation() -> None:
    sending = unit_sending_v4_reply_receipt()
    sending["confirmation_epoch"] = sending["attempt_epoch"]

    assert bot.sending_reply_receipt_is_semantically_valid(sending) is False


def test_schema_v4_mention_receipt_accepts_pagination_provenance() -> None:
    sending = unit_sending_v4_reply_receipt()
    sending["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "page-4",
    }
    confirmed = unit_confirmed_v4_reply_receipt()
    confirmed["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "page-4",
    }

    assert bot.sending_reply_receipt_is_semantically_valid(sending) is True
    assert bot.confirmed_reply_receipt_is_semantically_valid(confirmed) is True


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_reply_post_helper_captures_confirmation_after_remote_success(
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
) -> None:
    attempt_epoch = int(datetime(2026, 7, 6, 23, 59, 50).timestamp())
    confirmation_epoch = int(datetime(2026, 7, 7, 0, 0, 5).timestamp())
    clock = {"epoch": attempt_epoch}
    monkeypatch.setattr(bot, "now_epoch", lambda: clock["epoch"])
    sending = bot.bind_conversational_reply_attempt_time(
        unit_v4_reply_receipt_template(lane=lane)
    )

    def confirmed_remote(
        *_args: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        assert bot.load_confirmed_reply_receipt() == ("sending", sending)
        clock["epoch"] = confirmation_epoch
        return {"data": {"id": "999"}}

    install_receipt_bound_x_request_stub(monkeypatch, confirmed_remote)
    response, confirmed = bot.post_conversational_reply_with_durable_identity(
        state=bot.default_state(),
        receipt_template=sending,
        reply_text=str(sending["reply_text"]),
        reply_to_id=str(sending["target_id"]),
        made_with_ai=False,
        lane=lane,
    )

    assert response == {"data": {"id": "999"}}
    assert confirmed["attempt_epoch"] == attempt_epoch
    assert confirmed["confirmation_epoch"] == confirmation_epoch
    assert confirmed["reply_epoch"] == confirmation_epoch
    assert confirmed["daily_reply_date"] == bot.epoch_date_str(
        confirmation_epoch
    )
    assert bot.load_confirmed_reply_receipt() == ("valid", confirmed)


def test_schema_v4_clock_rollback_uses_conservative_confirmation_time(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempt_epoch = 2_000_000_000
    clock = {"epoch": attempt_epoch}
    monkeypatch.setattr(bot, "now_epoch", lambda: clock["epoch"])
    sending = bot.bind_conversational_reply_attempt_time(
        unit_v4_reply_receipt_template()
    )

    def confirmed_after_clock_rollback(
        *_args: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        clock["epoch"] = attempt_epoch - 60
        return {"data": {"id": "999"}}

    install_receipt_bound_x_request_stub(
        monkeypatch,
        confirmed_after_clock_rollback,
    )
    _, confirmed = bot.post_conversational_reply_with_durable_identity(
        state=bot.default_state(),
        receipt_template=sending,
        reply_text=str(sending["reply_text"]),
        reply_to_id=str(sending["target_id"]),
        made_with_ai=False,
        lane="mention",
    )

    assert confirmed["attempt_epoch"] == attempt_epoch
    assert confirmed["confirmation_epoch"] == attempt_epoch
    assert confirmed["reply_epoch"] == attempt_epoch
    assert bot.confirmed_reply_receipt_is_semantically_valid(confirmed) is True


def test_invalid_v4_confirmation_never_mutates_fallback_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_000)
    sending = bot.bind_conversational_reply_attempt_time(
        unit_v4_reply_receipt_template()
    )
    state = bot.default_state()
    baseline = copy.deepcopy(state)
    original_validator = bot.confirmed_reply_receipt_is_semantically_valid

    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *_args, **_kwargs: {"data": {"id": "999"}},
    )
    monkeypatch.setattr(
        bot,
        "confirmed_reply_receipt_is_semantically_valid",
        lambda receipt: (
            False
            if receipt.get("schema_version") == 4
            else original_validator(receipt)
        ),
    )

    with pytest.raises(bot.UnrecoverableConfirmedReplyPersistenceError):
        bot.post_conversational_reply_with_durable_identity(
            state=state,
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    assert state == baseline
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)


def test_schema_v4_promotion_failure_fallback_uses_confirmation_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt_epoch = int(datetime(2026, 7, 6, 23, 59, 50).timestamp())
    confirmation_epoch = int(datetime(2026, 7, 7, 0, 0, 5).timestamp())
    clock = {"epoch": attempt_epoch}
    monkeypatch.setattr(bot, "now_epoch", lambda: clock["epoch"])
    sending = bot.bind_conversational_reply_attempt_time(
        unit_v4_reply_receipt_template()
    )
    state = bot.default_state()
    state["daily_reply_date"] = bot.epoch_date_str(attempt_epoch)
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)

    def confirmed_remote(
        *_args: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        clock["epoch"] = confirmation_epoch
        return {"data": {"id": "999"}}

    install_receipt_bound_x_request_stub(monkeypatch, confirmed_remote)
    monkeypatch.setattr(
        bot,
        "promote_sending_reply_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("promotion failed")
        ),
    )

    with pytest.raises(bot.ConfirmedReplyLocalPersistenceError):
        bot.post_conversational_reply_with_durable_identity(
            state=state,
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    assert state["last_reply_epoch"] == confirmation_epoch
    assert state["daily_reply_date"] == bot.epoch_date_str(confirmation_epoch)
    assert state["daily_reply_count"] == 1
    assert state["ai_reply_history"][0]["attempt_epoch"] == attempt_epoch
    assert state["ai_reply_history"][0]["confirmation_epoch"] == confirmation_epoch
    assert bot.load_confirmed_reply_receipt() == ("absent", None)


@pytest.mark.parametrize(
    ("remote_error", "expected_exception"),
    [
        (
            bot.ApiError("definite failure", service="x", status_code=400),
            bot.AmbiguousRemotePostOutcome,
        ),
        (
            bot.AmbiguousRemotePostOutcome("uncertain", service="x"),
            bot.AmbiguousRemotePostOutcome,
        ),
    ],
)
def test_schema_v4_failed_remote_outcome_never_invents_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    remote_error: Exception,
    expected_exception: type[Exception],
) -> None:
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_000)
    sending = bot.bind_conversational_reply_attempt_time(
        unit_v4_reply_receipt_template()
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(remote_error),
    )

    with pytest.raises(expected_exception):
        bot.post_conversational_reply_with_durable_identity(
            state=bot.default_state(),
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    status, receipt = bot.load_confirmed_reply_receipt()
    assert status == "sending"
    if receipt is not None:
        assert "confirmation_epoch" not in receipt
        assert receipt["reply_epoch"] == receipt["attempt_epoch"]


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_schema_v4_confirmation_advances_daily_counters_once_across_midnight(
    lane: str,
) -> None:
    attempt_epoch = int(datetime(2026, 7, 6, 23, 59, 50).timestamp())
    confirmation_epoch = int(datetime(2026, 7, 7, 0, 0, 5).timestamp())
    receipt = unit_confirmed_v4_reply_receipt(
        lane=lane,
        attempt_epoch=attempt_epoch,
        confirmation_epoch=confirmation_epoch,
        author_id="200",
    )
    state = bot.default_state()
    state["daily_reply_date"] = bot.epoch_date_str(attempt_epoch)
    state["daily_reply_count"] = 7
    state["daily_replied_author_ids"] = ["old-author"]
    state["daily_replied_author_counts"] = {"old-author": 2}
    state["daily_quote_reply_date"] = bot.epoch_date_str(attempt_epoch)
    state["daily_quote_reply_count"] = 3

    bot.apply_confirmed_reply_receipt(state, receipt)
    bot.apply_confirmed_reply_receipt(state, receipt)

    confirmation_date = bot.epoch_date_str(confirmation_epoch)
    assert state["daily_reply_date"] == confirmation_date
    assert state["daily_reply_count"] == 1
    assert state["daily_replied_author_ids"] == ["200"]
    assert state["daily_replied_author_counts"] == {"200": 1}
    assert state["last_reply_epoch"] == confirmation_epoch
    assert state["ai_reply_history"][0]["attempt_epoch"] == attempt_epoch
    assert state["ai_reply_history"][0]["confirmation_epoch"] == confirmation_epoch
    assert state["ai_reply_history"][0]["reply_epoch"] == confirmation_epoch
    assert state["tweet_cache"]["999"]["created_at"] == datetime.fromtimestamp(
        confirmation_epoch
    ).isoformat()
    if lane == "quote_tweet":
        assert state["daily_quote_reply_date"] == confirmation_date
        assert state["daily_quote_reply_count"] == 1
    else:
        assert state["daily_quote_reply_date"] == bot.epoch_date_str(attempt_epoch)
        assert state["daily_quote_reply_count"] == 3


def test_confirmed_factual_reply_outcome_logs_existing_evidence_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **values: events.append((name, values)),
    )
    receipt = unit_confirmed_reply_receipt(
        factual=True,
        text=(
            "People moved from East Germany towards West Germany in November "
            "1989."
        ),
    )

    bot.apply_confirmed_reply_receipt(bot.default_state(), receipt)

    outcome = next(
        values
        for name, values in events
        if name == "ai_reply_pipeline_outcome"
    )
    assert outcome["status"] == "confirmed"
    assert outcome["evidence_confidence"] == "high"
    assert outcome["retrieved_count"] == 1
    assert outcome["evidence_reference_count"] == 1


def test_schema_v4_reconciliation_never_rolls_newer_daily_state_backward() -> None:
    attempt_epoch = int(datetime(2026, 7, 6, 23, 59, 50).timestamp())
    confirmation_epoch = int(datetime(2026, 7, 7, 0, 0, 5).timestamp())
    receipt = unit_confirmed_v4_reply_receipt(
        lane="quote_tweet",
        attempt_epoch=attempt_epoch,
        confirmation_epoch=confirmation_epoch,
    )
    state = bot.default_state()
    state["daily_reply_date"] = "2026-07-08"
    state["daily_reply_count"] = 4
    state["daily_replied_author_ids"] = ["later-author"]
    state["daily_replied_author_counts"] = {"later-author": 1}
    state["daily_quote_reply_date"] = "2026-07-08"
    state["daily_quote_reply_count"] = 2

    bot.apply_confirmed_reply_receipt(state, receipt)

    assert state["daily_reply_date"] == "2026-07-08"
    assert state["daily_reply_count"] == 4
    assert state["daily_replied_author_counts"] == {"later-author": 1}
    assert state["daily_quote_reply_date"] == "2026-07-08"
    assert state["daily_quote_reply_count"] == 2


def test_reply_spacing_is_measured_from_schema_v4_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt_epoch = 2_000_000_000
    confirmation_epoch = attempt_epoch + 120
    state = bot.default_state()
    state["daily_reply_date"] = bot.epoch_date_str(confirmation_epoch)
    state["daily_reply_count"] = 0
    bot.apply_confirmed_reply_receipt(
        state,
        unit_confirmed_v4_reply_receipt(
            attempt_epoch=attempt_epoch,
            confirmation_epoch=confirmation_epoch,
        ),
    )
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setitem(bot.ai_first_reply_strategy, "enabled", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 1800)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        bot,
        "now_epoch",
        lambda: confirmation_epoch + 1799,
    )
    monkeypatch.setattr(
        bot,
        "current_datetime",
        lambda: datetime.fromtimestamp(confirmation_epoch + 1799),
    )
    monkeypatch.setattr(
        bot,
        "get_mentions",
        lambda _state: pytest.fail("spacing must block candidate retrieval"),
    )

    assert (
        bot.maybe_reply_to_mentions(state)
        == bot.NORMAL_CHECK_STATUS_SKIPPED_SPACING
    )


@pytest.mark.parametrize(
    "pagination",
    [
        {"base_since_id": "", "next_token": "page-4"},
        {"base_since_id": "99", "next_token": "page-4"},
    ],
)
def test_schema_v3_mention_receipt_accepts_exact_pagination_provenance(
    pagination: dict[str, str],
) -> None:
    sending = unit_sending_reply_receipt()
    sending["mention_pagination"] = pagination
    confirmed = {
        **sending,
        "lifecycle_state": "confirmed",
        "reply_post_id": "999",
    }

    assert bot.sending_reply_receipt_is_semantically_valid(sending) is True
    assert bot.confirmed_reply_receipt_is_semantically_valid(confirmed) is True


@pytest.mark.parametrize(
    "pagination",
    [
        None,
        [],
        {},
        {"base_since_id": "99"},
        {"next_token": "page-4"},
        {"base_since_id": "99", "next_token": ""},
        {"base_since_id": "not-a-post-id", "next_token": "page-4"},
        {"base_since_id": 99, "next_token": "page-4"},
        {"base_since_id": "99", "next_token": 15},
        {
            "base_since_id": "99",
            "next_token": "page-4",
            "unexpected": "field",
        },
    ],
)
def test_schema_v3_mention_receipt_rejects_malformed_pagination_provenance(
    pagination: object,
) -> None:
    sending = unit_sending_reply_receipt()
    sending["mention_pagination"] = pagination

    assert bot.sending_reply_receipt_is_semantically_valid(sending) is False


@pytest.mark.parametrize("lane", ["hot_post_reply", "quote_tweet"])
def test_schema_v3_nonmention_receipt_rejects_mention_pagination_provenance(
    lane: str,
) -> None:
    sending = unit_sending_reply_receipt(lane=lane)
    sending["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "page-4",
    }

    assert bot.sending_reply_receipt_is_semantically_valid(sending) is False


def test_legacy_schema_v2_receipt_rejects_new_pagination_provenance() -> None:
    receipt = unit_confirmed_reply_receipt()
    receipt["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "page-4",
    }

    assert bot.confirmed_reply_receipt_is_semantically_valid(receipt) is False


def test_confirmed_mention_receipt_restores_pagination_without_advancing_watermark() -> None:
    pagination = {
        "base_since_id": "99",
        "next_token": "page-4",
    }
    receipt = unit_confirmed_v3_reply_receipt(target_id="100")
    receipt["mention_pagination"] = pagination
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_pagination"] = {}

    bot.apply_confirmed_reply_receipt(state, receipt)
    bot.apply_confirmed_reply_receipt(state, receipt)

    assert state["last_seen_mention_id"] == "99"
    assert state["mention_pagination"] == pagination
    assert state["replied_to_ids"].count("100") == 1
    assert state["own_auto_reply_ids"].count("999") == 1


def test_confirmed_truncated_mention_receipt_reconciles_after_restart_without_x(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    receipt = unit_confirmed_v3_reply_receipt(
        target_id="100",
        reply_post_id="999",
    )
    receipt["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "page-4",
    }
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda *_args, **_kwargs: pytest.fail(
            "confirmed receipt reconciliation must not repeat the X write"
        ),
    )

    bot.write_confirmed_reply_receipt(receipt)
    assert bot.reconcile_confirmed_reply_receipt(state) is True

    assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    assert state["last_seen_mention_id"] == "99"
    assert state["mention_pagination"] == {
        "base_since_id": "99",
        "next_token": "page-4",
    }
    assert state["replied_to_ids"] == ["100"]
    assert state["own_auto_reply_ids"] == ["999"]


def test_confirmed_mention_receipt_rejects_pagination_base_mismatch() -> None:
    receipt = unit_confirmed_v3_reply_receipt(target_id="100")
    receipt["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "page-4",
    }
    state = bot.default_state()
    state["last_seen_mention_id"] = "98"

    with pytest.raises(
        bot.InvalidConfirmedReplyReceipt,
        match="pagination base does not match",
    ):
        bot.apply_confirmed_reply_receipt(state, receipt)

    assert state["last_seen_mention_id"] == "98"
    assert state["mention_pagination"] == {}


@pytest.mark.parametrize("schema_version", [2, 3])
def test_legacy_mention_receipt_without_pagination_remains_valid_and_advances_watermark(
    schema_version: int,
) -> None:
    if schema_version == 2:
        receipt = unit_confirmed_reply_receipt(target_id="100")
    else:
        receipt = unit_confirmed_v3_reply_receipt(target_id="100")
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"

    assert bot.confirmed_reply_receipt_is_semantically_valid(receipt) is True
    bot.apply_confirmed_reply_receipt(state, receipt)

    assert state["last_seen_mention_id"] == "100"
    assert state["mention_pagination"] == {}


@pytest.mark.parametrize("schema_version", [2, 3])
def test_legacy_mention_receipt_preserves_matching_active_pagination(
    schema_version: int,
) -> None:
    if schema_version == 2:
        receipt = unit_confirmed_reply_receipt(target_id="100")
    else:
        receipt = unit_confirmed_v3_reply_receipt(target_id="100")
    pagination = {
        "base_since_id": "99",
        "next_token": "page-4",
    }
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_pagination"] = pagination

    bot.apply_confirmed_reply_receipt(state, receipt)

    assert state["last_seen_mention_id"] == "99"
    assert state["mention_pagination"] == pagination


def test_conversational_reply_receipt_is_durable_before_remote_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_v4_reply_receipt()
    state = bot.default_state()
    observed: list[dict[str, object]] = []

    def confirmed_remote(
        method: str,
        path: str,
        **kwargs: object,
    ) -> dict[str, object]:
        status, current = bot.load_confirmed_reply_receipt()
        assert status == "sending"
        assert current == sending
        observed.append({"method": method, "path": path, **kwargs})
        return {"data": {"id": "999"}}

    install_receipt_bound_x_request_stub(monkeypatch, confirmed_remote)

    response, confirmed = bot.post_conversational_reply_with_durable_identity(
        state=state,
        receipt_template=sending,
        reply_text=str(sending["reply_text"]),
        reply_to_id=str(sending["target_id"]),
        made_with_ai=False,
        lane="mention",
    )

    assert response == {"data": {"id": "999"}}
    assert confirmed["lifecycle_state"] == "confirmed"
    assert confirmed["reply_post_id"] == "999"
    assert bot.load_confirmed_reply_receipt() == ("valid", confirmed)
    assert observed[0]["method"] == "POST"
    assert observed[0]["path"] == "/2/tweets"


def test_prepared_reply_bypass_requires_exact_receipt_text_and_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_reply_receipt()
    bot.write_sending_reply_receipt(sending)
    remote_calls = 0

    def remote(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal remote_calls
        remote_calls += 1
        return {"data": {"id": "999"}}

    install_receipt_bound_x_request_stub(monkeypatch, remote)

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.create_post(
            str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
        )
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="does not exactly bind"):
        bot.create_post(
            "Different reply text.",
            reply_to_id=str(sending["target_id"]),
            prepared_conversational_reply_receipt=sending,
        )
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="does not exactly bind"):
        bot.create_post(
            str(sending["reply_text"]),
            reply_to_id="101",
            prepared_conversational_reply_receipt=sending,
        )
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="does not exactly bind"):
        bot.create_post(
            str(sending["reply_text"]),
            media_ids=["media-1"],
            reply_to_id=str(sending["target_id"]),
            prepared_conversational_reply_receipt=sending,
        )

    assert remote_calls == 0


@pytest.mark.parametrize(
    "lane",
    [
        "regular_quote",
        "meme",
        "mention",
        "quote_tweet",
        "historical_context",
        "direct_create",
        "direct_media_upload",
    ],
)
def test_sending_reply_receipt_blocks_each_remote_lane_before_preparation(
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
) -> None:
    import historical_context_formatter

    sending = unit_sending_reply_receipt()
    bot.write_sending_reply_receipt(sending)
    calls: list[str] = []

    def prepared(name: str) -> None:
        calls.append(name)
        pytest.fail(f"{name} preparation must not run through a sending receipt")

    if lane == "regular_quote":
        monkeypatch.setattr(
            bot,
            "reconcile_main_post_receipts",
            lambda *_args: prepared("receipt reconciliation"),
        )
        invoke = lambda: bot.post_random_quote(set(), set(), bot.default_state())
    elif lane == "meme":
        monkeypatch.setattr(
            bot,
            "both_main_post_receipts_exist",
            lambda: prepared("meme receipt check"),
        )
        invoke = lambda: bot.post_next_meme(bot.default_state())
    elif lane == "mention":
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(
            bot,
            "get_mentions",
            lambda *_args: prepared("mention fetch"),
        )
        invoke = lambda: bot.maybe_reply_to_mentions(bot.default_state())
    elif lane == "quote_tweet":
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
        monkeypatch.setattr(
            bot,
            "build_quote_lookup_post_ids",
            lambda *_args: prepared("quote lookup"),
        )
        invoke = lambda: bot.maybe_reply_to_quote_tweets(bot.default_state())
    elif lane == "historical_context":
        monkeypatch.setattr(
            bot,
            "historical_context_reply",
            {**bot.historical_context_reply, "enabled": True},
        )
        monkeypatch.setattr(
            historical_context_formatter,
            "load_and_validate_corpus",
            lambda *_args: prepared("historical research load"),
        )
        invoke = lambda: bot.maybe_post_historical_context_reply(
            quote_hash="a" * 64,
            quote_text="Quote",
            parent_post_id="123",
        )
    elif lane == "direct_create":
        monkeypatch.setattr(
            bot,
            "x_request",
            lambda *_args, **_kwargs: prepared("X request"),
        )
        invoke = lambda: bot.create_post("test")
    else:
        monkeypatch.setattr(
            bot,
            "upload_media_v2",
            lambda *_args, **_kwargs: prepared("media upload"),
        )
        monkeypatch.setattr(
            bot,
            "upload_media_v1_1",
            lambda *_args, **_kwargs: prepared("media upload fallback"),
        )
        invoke = lambda: bot.upload_media("image.png", lane="quote_image")

    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="unresolved conversational-reply",
    ):
        invoke()

    assert calls == []


def test_conversational_reply_template_must_match_declared_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_v4_reply_receipt(lane="mention")
    monkeypatch.setattr(
        bot,
        "x_request",
        lambda *_args, **_kwargs: pytest.fail("invalid lane must fail before X"),
    )

    with pytest.raises(RuntimeError, match="invalid reply receipt template"):
        bot.post_conversational_reply_with_durable_identity(
            state=bot.default_state(),
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="quote_tweet",
        )

    assert bot.load_confirmed_reply_receipt() == ("absent", None)


def test_conversational_post_rejects_legacy_receipt_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_reply_receipt()
    monkeypatch.setattr(
        bot,
        "x_request",
        lambda *_args, **_kwargs: pytest.fail(
            "legacy conversational receipt must fail before transport"
        ),
    )

    with pytest.raises(RuntimeError, match="current schema-v4"):
        bot.post_conversational_reply_with_durable_identity(
            state=bot.default_state(),
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    assert bot.load_confirmed_reply_receipt() == ("absent", None)


def test_generic_reply_rejection_preserves_sending_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_v4_reply_receipt()

    def rejected(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise bot.ApiError(
            "X API error 403: reply forbidden",
            service="x",
            status_code=403,
        )

    install_receipt_bound_x_request_stub(monkeypatch, rejected)

    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="outcome is unproved"):
        bot.post_conversational_reply_with_durable_identity(
            state=bot.default_state(),
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    status, receipt = bot.load_confirmed_reply_receipt()
    assert status == "sending"
    assert receipt == sending


@pytest.mark.parametrize(
    "remote_error",
    [
        RuntimeError("unexpected transport implementation failure"),
        ValueError("response decoder failed after accepted write"),
        KeyboardInterrupt(),
    ],
)
def test_unclassified_reply_interruption_preserves_sending_receipt(
    monkeypatch: pytest.MonkeyPatch,
    remote_error: BaseException,
) -> None:
    sending = unit_sending_v4_reply_receipt()

    def interrupted(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise remote_error

    install_receipt_bound_x_request_stub(monkeypatch, interrupted)
    expected = (
        bot.AmbiguousRemotePostOutcome
        if isinstance(remote_error, Exception)
        else type(remote_error)
    )

    with pytest.raises(expected):
        bot.post_conversational_reply_with_durable_identity(
            state=bot.default_state(),
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    assert bot.load_confirmed_reply_receipt() == ("sending", sending)
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    assert bot.ambiguous_remote_post_is_blocking() is True


def test_reply_ambiguity_marker_and_state_failure_preserve_restart_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_v4_reply_receipt()
    original_atomic_write = bot.atomic_write_json
    remote_calls = 0

    def selective_atomic_write(
        path: Path,
        data: object,
        **kwargs: object,
    ) -> None:
        if path == bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE:
            raise OSError("marker failed")
        original_atomic_write(path, data, **kwargs)

    def accepted_without_response(
        *_args: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        nonlocal remote_calls
        remote_calls += 1
        raise bot.AmbiguousRemotePostOutcome(
            "accepted then connection closed",
            service="x",
        )

    monkeypatch.setattr(bot, "atomic_write_json", selective_atomic_write)
    install_receipt_bound_x_request_stub(monkeypatch, accepted_without_response)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("state failed")),
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_conversational_reply_with_durable_identity(
            state=bot.default_state(),
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    assert remote_calls == 1
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)

    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.create_post("must not be sent")
    assert remote_calls == 1


def test_reply_promotion_state_and_marker_failure_blocks_restart_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_v4_reply_receipt()
    original_atomic_write = bot.atomic_write_json
    remote_calls = 0

    def selective_atomic_write(
        path: Path,
        data: object,
        **kwargs: object,
    ) -> None:
        if path == bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE:
            raise OSError("marker failed")
        original_atomic_write(path, data, **kwargs)

    def confirmed_remote(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal remote_calls
        remote_calls += 1
        return {"data": {"id": "999"}}

    monkeypatch.setattr(bot, "atomic_write_json", selective_atomic_write)
    install_receipt_bound_x_request_stub(monkeypatch, confirmed_remote)
    monkeypatch.setattr(
        bot,
        "promote_sending_reply_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("promotion failed")
        ),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("state failed")),
    )

    with pytest.raises(bot.UnrecoverableConfirmedReplyPersistenceError):
        bot.post_conversational_reply_with_durable_identity(
            state=bot.default_state(),
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    assert remote_calls == 1
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)

    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.create_post("must not be sent")
    assert remote_calls == 1


def test_reply_promotion_failure_uses_confirmed_state_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sending = unit_sending_v4_reply_receipt()
    state = bot.default_state()
    state["daily_reply_date"] = str(sending["daily_reply_date"])
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *_args, **_kwargs: {"data": {"id": "999"}},
    )
    monkeypatch.setattr(
        bot,
        "promote_sending_reply_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("promotion failed")
        ),
    )

    with pytest.raises(bot.ConfirmedReplyLocalPersistenceError):
        bot.post_conversational_reply_with_durable_identity(
            state=state,
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    assert bot.load_confirmed_reply_receipt() == ("absent", None)
    assert bot.confirmed_reply_emergency_representation_is_complete(
        bot._confirmed_reply_receipt_from_sending(
            sending,
            reply_post_id="999",
            confirmation_epoch=int(sending["attempt_epoch"]),
        ),
        state,
    )
    assert bot.json_file_matches(bot.STATE_FILE, state) is True
    assert "disposition=confirmed_state_fallback" in caplog.text
    assert "after definite non-success" not in caplog.text


def test_reply_sigint_is_delivered_only_after_confirmed_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_v4_reply_receipt()
    state = bot.default_state()
    remote_calls = 0
    guard = object()

    def confirmed_remote(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal remote_calls
        remote_calls += 1
        assert bot.load_confirmed_reply_receipt() == ("sending", sending)
        return {"data": {"id": "999"}}

    def deliver_sigint(actual_guard: object) -> None:
        assert actual_guard is guard
        status, receipt = bot.load_confirmed_reply_receipt()
        assert status == "valid"
        assert receipt is not None
        assert receipt["lifecycle_state"] == "confirmed"
        assert receipt["reply_post_id"] == "999"
        raise KeyboardInterrupt

    install_receipt_bound_x_request_stub(monkeypatch, confirmed_remote)
    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", lambda: guard)
    monkeypatch.setattr(bot, "end_confirmed_post_sigint_deferral", deliver_sigint)

    with pytest.raises(KeyboardInterrupt):
        bot.post_conversational_reply_with_durable_identity(
            state=state,
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    assert remote_calls == 1
    assert bot.reconcile_confirmed_reply_receipt(state) is True
    assert bot.load_confirmed_reply_receipt() == ("absent", None)
    assert state["replied_to_ids"] == ["100"]
    assert state["own_auto_reply_ids"] == ["999"]


def test_reply_post_return_inspection_failure_restores_guard_and_keeps_barriers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_v4_reply_receipt()
    state = bot.default_state()
    baseline = copy.deepcopy(state)
    guard = object()
    ended: list[object] = []
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *_args, **_kwargs: {"data": {"id": "999"}},
    )
    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", lambda: guard)
    monkeypatch.setattr(
        bot,
        "end_confirmed_post_sigint_deferral",
        lambda actual: ended.append(actual),
    )
    monkeypatch.setattr(
        bot,
        "inspect_confirmed_transport_transaction",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            bot.TransportJournalError("injected post-return inspection failure")
        ),
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="identity could not"):
        bot.post_conversational_reply_with_durable_identity(
            state=state,
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    assert ended == [guard]
    assert state == baseline
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)
    assert bot.inspect_transport_state(
        bot.journal_path_for_receipt(bot.CONFIRMED_REPLY_RECEIPT_FILE)
    ).blocking


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_reply_sigint_during_confirmed_promotion_reconciles_without_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
) -> None:
    sending = unit_sending_v4_reply_receipt(lane=lane)
    original_replace = bot.replace_bound_source_receipt
    original_begin = bot.begin_confirmed_post_sigint_deferral
    prior_handler = signal.getsignal(signal.SIGINT)
    guard_holder: dict[str, bot.ConfirmedPostSigintDeferral] = {}
    remote_calls = 0

    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)

    def begin_deferral() -> bot.ConfirmedPostSigintDeferral:
        guard = original_begin()
        guard_holder["guard"] = guard
        return guard

    def interrupt_after_confirmed_promotion(
        binding: object,
        replacement: bytes,
        **kwargs: object,
    ) -> None:
        original_replace(binding, replacement, **kwargs)
        guard_holder["guard"].handle(signal.SIGINT, None)

    def confirmed_remote(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal remote_calls
        remote_calls += 1
        return {"data": {"id": "999"}}

    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", begin_deferral)
    monkeypatch.setattr(
        bot,
        "replace_bound_source_receipt",
        interrupt_after_confirmed_promotion,
    )
    install_receipt_bound_x_request_stub(monkeypatch, confirmed_remote)

    try:
        with pytest.raises(KeyboardInterrupt):
            bot.post_conversational_reply_with_durable_identity(
                state=bot.default_state(),
                receipt_template=sending,
                reply_text=str(sending["reply_text"]),
                reply_to_id=str(sending["target_id"]),
                made_with_ai=False,
                lane=lane,
            )
    finally:
        signal.signal(signal.SIGINT, prior_handler)

    status, receipt = bot.load_confirmed_reply_receipt()
    assert status == "valid"
    assert receipt is not None
    assert receipt["reply_post_id"] == "999"
    assert remote_calls == 1

    restarted_state = bot.default_state()
    restarted_state["daily_reply_date"] = str(receipt["daily_reply_date"])
    if lane == "quote_tweet":
        restarted_state["daily_quote_reply_date"] = str(
            receipt["daily_quote_reply_date"]
        )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda *_args, **_kwargs: pytest.fail(
            "confirmed receipt restart reconciliation must not repeat X"
        ),
    )

    assert bot.reconcile_confirmed_reply_receipt(restarted_state) is True
    assert bot.load_confirmed_reply_receipt() == ("absent", None)
    assert restarted_state["own_auto_reply_ids"] == ["999"]
    if lane == "quote_tweet":
        assert restarted_state["replied_to_quote_post_ids"] == ["100"]
        assert restarted_state["seen_quote_post_ids"] == ["100"]
    else:
        assert restarted_state["replied_to_ids"] == ["100"]
    assert remote_calls == 1


def test_remove_reply_receipt_refuses_changed_transaction() -> None:
    sending = unit_sending_reply_receipt()
    bot.write_sending_reply_receipt(sending)
    changed = {**sending, "target_id": "101"}

    with pytest.raises(
        bot.InvalidConfirmedReplyReceipt,
        match="transaction identity changed",
    ):
        bot.remove_confirmed_reply_receipt(changed)

    assert bot.load_confirmed_reply_receipt() == ("sending", sending)


@pytest.mark.parametrize(
    "lane",
    [
        "quote_image",
        "daily_meme",
        "conversational_reply",
        "historical_context_reply",
    ],
)
def test_final_receipt_cleanup_fsync_failure_latches_every_public_lane(
    lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pathname absence cannot reopen provider work after uncertain cleanup."""

    if lane == "quote_image":
        path = bot.REGULAR_POST_RECEIPT_FILE
        receipt = {"unit": lane}
        bot.atomic_write_json(path, receipt, durable=True)
        retire = lambda: bot.remove_regular_post_receipt(receipt)
    elif lane == "daily_meme":
        path = bot.MEME_POST_RECEIPT_FILE
        receipt = {"unit": lane}
        bot.atomic_write_json(path, receipt, durable=True)
        retire = lambda: bot.remove_meme_post_receipt(receipt)
    elif lane == "conversational_reply":
        path = bot.CONFIRMED_REPLY_RECEIPT_FILE
        receipt = unit_confirmed_reply_receipt()
        bot.atomic_write_json(path, receipt, durable=True)
        retire = lambda: bot.remove_confirmed_reply_receipt(receipt)
    else:
        from historical_context_formatter import HistoricalContextReplyStore

        path = bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
        receipt = {"unit": lane}
        bot.atomic_write_json(path, receipt, durable=True)
        store = HistoricalContextReplyStore(
            tmp_path / "context-history.json",
            path,
            mutation_authority_provider=bot.transaction_mutation_authority,
            retirement_uncertainty_callback=(
                bot.latch_source_receipt_retirement_uncertainty
            ),
        )
        retire = lambda: store._retire_exact_receipt(path.read_bytes())

    paths = exact_retirement_module.retirement_barrier_paths(path)
    real_fsync = exact_retirement_module._fsync_directory

    def fail_after_final_namespace_cleanup(directory_fd: int) -> None:
        real_fsync(directory_fd)
        if not any(os.path.lexists(candidate) for candidate in paths):
            raise OSError("final receipt namespace fsync failed")

    monkeypatch.setattr(
        exact_retirement_module,
        "_fsync_directory",
        fail_after_final_namespace_cleanup,
    )

    with pytest.raises(OSError, match="final receipt namespace fsync failed"):
        retire()

    assert not any(os.path.lexists(candidate) for candidate in paths)
    assert bot.remote_write_safety_incident_is_latched() is True
    assert bot.ambiguous_remote_post_is_blocking() is True
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.require_remote_operation_unpaused("auxiliary provider request")


def test_same_thread_clarification_bypasses_author_cap_once_and_becomes_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    state = bot.default_state()
    state["daily_reply_date"] = datetime.fromtimestamp(fixed_epoch).strftime("%Y-%m-%d")
    state["daily_reply_count"] = 1
    state["daily_replied_author_ids"] = ["200"]
    state["daily_replied_author_counts"] = {"200": 1}
    state["own_auto_reply_ids"] = ["900"]
    state["tweet_cache"] = {
        "100": {
            "id": "100", "author_id": "200", "conversation_id": "700",
            "text": "@MrsMThatcher Where did people run towards when the Berlin Wall fell?",
            "referenced_tweets": [{"type": "replied_to", "id": "700"}],
        },
        "900": {
            "id": "900", "author_id": "12345", "conversation_id": "700",
            "text": "When free to choose, people choose freedom.",
            "post_type": "auto_reply",
            "referenced_tweets": [{"type": "replied_to", "id": "100"}],
        },
    }
    correction = {
        "id": "101", "author_id": "200", "conversation_id": "700",
        "text": "@MrsMThatcher That did not answer my question.",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "referenced_tweets": [{"type": "replied_to", "id": "900"}],
    }
    second_follow_up = {
        "id": "102", "author_id": "200", "conversation_id": "700",
        "text": "@MrsMThatcher And again?",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "referenced_tweets": [{"type": "replied_to", "id": "900001"}],
    }
    calls: list[dict[str, object]] = []

    def build_context(candidate: dict, _state: dict) -> tuple[dict[str, object], bool]:
        return unit_reply_context(
            target_id=str(candidate["id"]),
            thread_id=str(candidate["conversation_id"]),
            contribution=str(candidate["text"]),
        ), True

    def answer(context: dict[str, object], *_args: object, **_kwargs: object) -> AIReply:
        calls.append(context)
        return unit_approved_reply(
            context,
            text="People moved from East Berlin and East Germany towards West Berlin and West Germany.",
            mode="direct_factual_answer",
            factual=True,
        )

    current_candidates = [correction]
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    enabled_strategy = copy.deepcopy(bot.ai_first_reply_strategy)
    enabled_strategy["enabled"] = True
    monkeypatch.setattr(bot, "ai_first_reply_strategy", enabled_strategy)
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
    monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 1)
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: list(current_candidates))
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    monkeypatch.setattr(bot, "build_context_for_reply_ai", build_context)
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(bot, "generate_ai_first_reply", answer)
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *_args, **_kwargs: {"data": {"id": "900001"}},
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_POSTED
    assert len(calls) == 1
    assert calls[0]["clarification_request"]["original_question"].startswith(
        "@MrsMThatcher Where did people run"
    )
    assert state["daily_reply_count"] == 2
    assert state["clarification_reply_records"]["700"]["status"] == "repair_reply_completed"
    assert state["clarification_reply_records"]["700"]["thread_terminal"] is True

    current_candidates[:] = [second_follow_up]
    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert len(calls) == 1


def test_author_cap_context_is_terminal_but_available_to_next_eligible_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert bot.MAX_REPLIES_PER_AUTHOR_PER_DAY == 3
    clock = [2_000_000_000]
    state = bot.default_state()
    state["daily_reply_date"] = datetime.fromtimestamp(clock[0]).strftime("%Y-%m-%d")
    state["daily_reply_count"] = 3
    state["daily_replied_author_ids"] = ["200"]
    state["daily_replied_author_counts"] = {"200": 3}
    state["tweet_cache"] = {
        "100": {
            "id": "100",
            "cached_epoch": clock[0],
            "author_id": "12345",
            "conversation_id": "100",
            "text": "The opening contribution.",
            "referenced_tweets": [],
        },
    }
    capped = {
        "id": "200",
        "author_id": "200",
        "conversation_id": "100",
        "text": "Please also account for the effect on small businesses.",
        "created_at": "2026-01-01T12:00:00Z",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "referenced_tweets": [{"type": "replied_to", "id": "100"}],
    }
    eligible = {
        "id": "201",
        "author_id": "200",
        "conversation_id": "100",
        "text": "What practical policy follows from that?",
        "created_at": "2026-01-02T12:00:00Z",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "referenced_tweets": [{"type": "replied_to", "id": "100"}],
    }
    current_candidates = [capped]
    ai_contexts: list[dict[str, object]] = []

    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    enabled_strategy = copy.deepcopy(bot.ai_first_reply_strategy)
    enabled_strategy["enabled"] = True
    monkeypatch.setattr(bot, "ai_first_reply_strategy", enabled_strategy)
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
    monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 3)
    monkeypatch.setattr(bot, "now_epoch", lambda: clock[0])
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(clock[0]))
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: list(current_candidates))
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    monkeypatch.setattr(
        bot,
        "get_tweet_by_id",
        lambda *_args, **_kwargs: pytest.fail("context must use tweet_cache"),
    )
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {})

    def answer(context: dict[str, object], *_args: object, **_kwargs: object) -> AIReply:
        ai_contexts.append(context)
        return unit_approved_reply(context, text="A practical policy answer.")

    monkeypatch.setattr(bot, "generate_ai_first_reply", answer)
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *_args, **_kwargs: {"data": {"id": "900001"}},
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert ai_contexts == []
    assert state["last_seen_mention_id"] == "200"
    assert state["tweet_cache"]["200"]["post_type"] == "author_cap_context"

    clock[0] += 24 * 60 * 60
    current_candidates[:] = [eligible]
    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_POSTED
    assert len(ai_contexts) == 1
    assert ai_contexts[0]["target_id"] == "201"
    assert [post["post_id"] for post in ai_contexts[0]["parent_thread"]] == ["100", "200"]
    assert state["replied_to_ids"] == ["201"]


def test_unrelated_follow_up_does_not_bypass_author_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    state = bot.default_state()
    state["daily_reply_date"] = datetime.fromtimestamp(fixed_epoch).strftime("%Y-%m-%d")
    state["daily_reply_count"] = 1
    state["daily_replied_author_counts"] = {"200": 1}
    state["own_auto_reply_ids"] = ["900"]
    state["tweet_cache"] = {
        "100": {
            "id": "100", "author_id": "200", "conversation_id": "700",
            "text": "@MrsMThatcher Where did people run towards when the Berlin Wall fell?",
            "referenced_tweets": [],
        },
        "900": {
            "id": "900", "author_id": "12345", "conversation_id": "700",
            "text": "A prior reply.", "post_type": "auto_reply",
            "referenced_tweets": [{"type": "replied_to", "id": "100"}],
        },
    }
    follow_up = {
        "id": "101", "author_id": "200", "conversation_id": "700",
        "text": "@MrsMThatcher What is your favourite film?",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "referenced_tweets": [{"type": "replied_to", "id": "900"}],
    }
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 1)
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: [follow_up])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "generate_ai_first_reply", lambda *_args, **_kwargs: pytest.fail("xAI must not be called"))
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED


def test_clarification_ledger_survives_state_restart_and_blocks_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    state = bot.default_state()
    clarification_request = {
        "original_question": "Where did people move when the Berlin Wall fell?",
        "correction": "That did not answer my question.",
    }
    receipt = unit_confirmed_reply_receipt(
        target_id="101",
        reply_post_id="900001",
        author_id="200",
        contribution=clarification_request["correction"],
        text="People moved from East Berlin towards West Berlin.",
        epoch=fixed_epoch,
        factual=True,
        clarification_request=clarification_request,
        conversation_id="700",
    )
    receipt["clarification_reply"] = {
        "thread_id": "700",
        "prior_bot_reply_id": "900",
        "original_question_id": "100",
        "trigger": "explicit_correction",
    }
    assert bot.confirmed_reply_receipt_is_semantically_valid(receipt) is True
    bot.apply_confirmed_reply_receipt(state, receipt)
    bot.save_state(state, durable=True)

    recovered = bot.load_state()
    candidate = {"id": "102", "conversation_id": "700"}
    assert recovered["clarification_reply_records"]["700"]["clarification_reply_used"] is True
    assert bot.clarification_thread_is_terminal(recovered, candidate) is True
    assert bot.author_used_clarification_recently(recovered, "200", current=fixed_epoch + 60) is True


def test_author_clarification_window_expires_at_exactly_24_hours() -> None:
    completed_epoch = 2_000_000_000
    state = bot.default_state()
    state["clarification_reply_records"] = {
        "700": {
            "thread_id": "700",
            "author_id": "200",
            "completed_epoch": completed_epoch,
            "status": "repair_reply_completed",
            "clarification_reply_used": True,
            "thread_terminal": True,
        }
    }

    assert bot.author_used_clarification_recently(
        state,
        "200",
        current=completed_epoch + bot.CLARIFICATION_REPLY_WINDOW_SECONDS - 1,
    ) is True
    assert bot.author_used_clarification_recently(
        state,
        "200",
        current=completed_epoch + bot.CLARIFICATION_REPLY_WINDOW_SECONDS,
    ) is False


def test_completed_clarification_thread_stays_terminal_after_restart_and_cap_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed_epoch = 2_000_000_000
    later_epoch = completed_epoch + (2 * 24 * 60 * 60)
    state = bot.default_state()
    clarification_request = {
        "original_question": "Where did people move when the Berlin Wall fell?",
        "correction": "That did not answer my question.",
    }
    receipt = unit_confirmed_reply_receipt(
        target_id="101",
        reply_post_id="900001",
        author_id="200",
        contribution=clarification_request["correction"],
        text="People moved from East Berlin towards West Berlin.",
        epoch=completed_epoch,
        factual=True,
        clarification_request=clarification_request,
        conversation_id="700",
    )
    receipt["clarification_reply"] = {
        "thread_id": "700",
        "prior_bot_reply_id": "900",
        "original_question_id": "100",
        "trigger": "explicit_correction",
    }
    bot.apply_confirmed_reply_receipt(state, receipt)
    bot.save_state(state, durable=True)
    recovered = bot.load_state()

    terminal_thread_candidate = {
        "id": "102", "author_id": "200", "conversation_id": "700",
        "text": "@MrsMThatcher What happened next?",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "referenced_tweets": [{"type": "replied_to", "id": "900001"}],
    }
    unrelated_thread_candidate = {
        "id": "103", "author_id": "200", "conversation_id": "800",
        "text": "@MrsMThatcher A thoughtful observation in a different thread.",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "referenced_tweets": [{"type": "replied_to", "id": "800"}],
    }
    context_ids: list[str] = []
    media_ids: list[str] = []
    model_contexts: list[str] = []

    def build_context(candidate: dict, _state: dict) -> tuple[dict[str, object], bool]:
        context_ids.append(str(candidate["id"]))
        return unit_reply_context(
            target_id=str(candidate["id"]),
            thread_id=str(candidate["conversation_id"]),
            contribution=str(candidate["text"]),
        ), True

    def prepare_media(candidate: dict, **_kwargs: object) -> dict:
        media_ids.append(str(candidate["id"]))
        return {}

    def answer(context: dict[str, object], *_args: object, **_kwargs: object) -> AIReply:
        model_contexts.append(str(context["target_id"]))
        return unit_approved_reply(context, text="Quite so.", mode="courtesy")

    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
    monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 1)
    monkeypatch.setattr(bot, "now_epoch", lambda: later_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(later_epoch))
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        bot,
        "get_mentions",
        lambda _state: [terminal_thread_candidate, unrelated_thread_candidate],
    )
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    monkeypatch.setattr(bot, "build_context_for_reply_ai", build_context)
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", prepare_media)
    monkeypatch.setattr(bot, "generate_ai_first_reply", answer)
    install_receipt_bound_x_request_stub(
        monkeypatch,
        lambda *_args, **_kwargs: {"data": {"id": "900002"}},
    )

    assert bot.maybe_reply_to_mentions(recovered) == bot.NORMAL_CHECK_STATUS_POSTED
    assert context_ids == ["103"]
    assert media_ids == ["103"]
    assert model_contexts == ["103"]
    assert recovered["daily_reply_count"] == 1
    assert recovered["daily_replied_author_counts"] == {"200": 1}
    assert recovered["clarification_reply_records"]["700"]["thread_terminal"] is True


def test_completed_clarification_threads_are_not_evicted_from_terminal_ledger() -> None:
    state = bot.default_state()
    state["clarification_reply_records"] = {
        str(thread_id): {
            "thread_id": str(thread_id),
            "author_id": str(thread_id),
            "reply_post_id": str(900_000 + thread_id),
            "completed_epoch": thread_id,
            "status": "repair_reply_completed",
            "clarification_reply_used": True,
            "thread_terminal": True,
        }
        for thread_id in range(1, 2001)
    }
    clarification_request = {
        "original_question": "What happened?",
        "correction": "That did not answer the question.",
    }
    receipt = unit_confirmed_reply_receipt(
        target_id="3001",
        reply_post_id="903001",
        author_id="3001",
        contribution=clarification_request["correction"],
        text="A grounded direct answer.",
        epoch=3001,
        factual=True,
        clarification_request=clarification_request,
        conversation_id="3001",
    )
    receipt["clarification_reply"] = {
        "thread_id": "3001",
        "prior_bot_reply_id": "903000",
        "original_question_id": "3000",
        "trigger": "explicit_correction",
    }

    bot.apply_confirmed_reply_receipt(state, receipt)

    assert "1" in state["clarification_reply_records"]
    assert "3001" in state["clarification_reply_records"]
    assert len(state["clarification_reply_records"]) == 2001


def test_clarification_receipt_requires_grounded_direct_reply_metadata() -> None:
    receipt = {
        "schema_version": 1,
        "target_id": "101",
        "reply_post_id": "900001",
        "author_id": "200",
        "reply_epoch": 2_000_000_000,
        "daily_reply_date": "2033-05-18",
        "candidate_source": "mention",
        "conversation_id": "700",
        "reply_text": "A rhetorical diversion.",
        "clarification_reply": {
            "thread_id": "700",
            "prior_bot_reply_id": "900",
            "original_question_id": "100",
            "trigger": "explicit_correction",
        },
    }

    assert bot.confirmed_reply_receipt_is_semantically_valid(receipt) is False


def test_clarification_receipt_rejects_approved_non_factual_draft() -> None:
    request = {
        "original_question": "Where did people run when the Berlin Wall fell?",
        "correction": "That did not answer my question.",
    }
    receipt = unit_confirmed_reply_receipt(
        target_id="101",
        reply_post_id="900001",
        contribution=request["correction"],
        text="When free to choose, people choose freedom.",
        clarification_request=request,
        conversation_id="700",
    )
    receipt["clarification_reply"] = {
        "thread_id": "700",
        "prior_bot_reply_id": "900",
        "original_question_id": "100",
        "trigger": "explicit_correction",
    }

    assert bot.confirmed_reply_receipt_is_semantically_valid(receipt) is False


def test_confirmed_reply_receipt_preserves_ai_draft_after_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
    state = bot.default_state()
    state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")
    receipt = unit_confirmed_reply_receipt(
        target_id="100",
        reply_post_id="900000",
        author_id="200",
        text="A grounded reply.",
        epoch=fixed_epoch,
        factual=True,
    )

    bot.write_confirmed_reply_receipt(receipt)
    assert bot.reconcile_confirmed_reply_receipt(state) is True
    bot.write_confirmed_reply_receipt(receipt)
    assert bot.reconcile_confirmed_reply_receipt(state) is True

    assert len(state["ai_reply_history"]) == 1
    history = state["ai_reply_history"][0]
    assert history["target_id"] == "100"
    assert history["reply_post_id"] == "900000"
    assert history["strategy_version"] == STRATEGY_VERSION
    assert history["reviewer_verdict"] == "approve"
    assert history["mode"] == "direct_factual_answer"


def test_confirmed_reply_receipt_rejects_malformed_ai_draft() -> None:
    receipt = unit_confirmed_reply_receipt(text="A grounded reply.", factual=True)
    receipt["ai_reply_draft"]["proposed_reply"] = {"not": "a string"}
    assert bot.confirmed_reply_receipt_is_semantically_valid(receipt) is False

    receipt = unit_confirmed_reply_receipt(text="A grounded reply.", factual=True)
    receipt["ai_reply_draft"]["mode"] = []
    assert bot.confirmed_reply_receipt_is_semantically_valid(receipt) is False


def test_confirmed_reply_receipt_rejects_non_approved_reviewer_verdict() -> None:
    receipt = unit_confirmed_reply_receipt(text="An alleged correction.")
    receipt["ai_reply_draft"]["reviewer_verdict"] = "revise"
    assert bot.confirmed_reply_receipt_is_semantically_valid(receipt) is False


@pytest.mark.parametrize("field", ["source_hashes", "claim_evidence", "evidence_ids"])
def test_confirmed_reply_receipt_rejects_incomplete_or_changed_evidence(field: str) -> None:
    receipt = unit_confirmed_reply_receipt(text="A grounded reply.", factual=True)
    receipt["ai_reply_draft"][field] = {} if field == "source_hashes" else []
    assert bot.confirmed_reply_receipt_is_semantically_valid(receipt) is False


@pytest.mark.parametrize(
    ("outer_field", "bad_value"),
    [
        ("target_id", "101"),
        ("conversation_id", "101"),
        ("candidate_source", "hot_post_reply"),
    ],
)
def test_confirmed_reply_receipt_binds_outer_identity_to_approved_context(
    outer_field: str,
    bad_value: str,
) -> None:
    receipt = unit_confirmed_reply_receipt()
    receipt[outer_field] = bad_value

    assert bot.confirmed_reply_receipt_is_semantically_valid(receipt) is False


def test_confirmed_quote_tweet_receipt_binds_original_post_to_context() -> None:
    receipt = unit_confirmed_reply_receipt(lane="quote_tweet", original_post_id="900")
    receipt["original_post_id"] = "901"

    assert bot.confirmed_reply_receipt_is_semantically_valid(receipt) is False


@pytest.mark.parametrize("bad_value", [float("inf"), float("-inf"), 1.5, True])
def test_scheduler_epoch_rejects_non_integer_numeric_values(bad_value: object) -> None:
    state = {"last_reply_check_epoch": bad_value}

    assert bot.scheduler_epoch_from_state(
        state,
        "last_reply_check_epoch",
        current=2_000_000_000,
    ) == (0, True)
    assert state["last_reply_check_epoch"] == 0
    assert type(state["last_reply_check_epoch"]) is int


@pytest.mark.parametrize("raw_value", ["nan", "inf", "-inf"])
def test_request_timeout_rejects_non_finite_values(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
) -> None:
    monkeypatch.setenv("MRS_REQUEST_TIMEOUT_SECONDS", raw_value)

    assert bot.parse_request_timeout_seconds() == 60.0


@pytest.mark.parametrize("raw_value", ["60.0001", "120", "180", "10000"])
def test_request_timeout_rejects_values_beyond_service_shutdown_budget(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
) -> None:
    monkeypatch.setenv("MRS_REQUEST_TIMEOUT_SECONDS", raw_value)

    assert bot.parse_request_timeout_seconds() == 60.0


def test_request_timeout_accepts_maximum_safe_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MRS_REQUEST_TIMEOUT_SECONDS",
        str(bot.MAX_REQUEST_TIMEOUT_SECONDS),
    )

    assert bot.parse_request_timeout_seconds() == bot.MAX_REQUEST_TIMEOUT_SECONDS


def test_x_request_uses_one_combined_connect_and_read_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class EmptyResponse:
        status_code = 204
        headers: dict[str, str] = {}
        text = ""

    def fake_request(*args: object, **kwargs: object) -> EmptyResponse:
        captured["timeout"] = kwargs["timeout"]
        return EmptyResponse()

    monkeypatch.setattr(bot, "REQUEST_TIMEOUT_SECONDS", 60.0)
    monkeypatch.setattr(bot.requests, "request", fake_request)

    assert bot.x_request("GET", "/2/test") == {}
    timeout = captured["timeout"]

    assert timeout.total == 60.0
    assert timeout.connect_timeout == 10.0


def test_parse_tweet_id_rejects_oversized_numeric_value() -> None:
    assert bot.parse_tweet_id("9" * 5_000, context="test tweet") is None


def test_invalid_pagination_cursor_classifier_is_status_and_parameter_specific() -> None:
    unrelated_bad_request = bot.ApiError(
        'X API error 400: {"errors":[{"message":"Invalid query operator"}]}',
        service="x",
        status_code=400,
    )
    echoed_cursor_with_unrelated_error = bot.ApiError(
        (
            'X API error 400: {"errors":[{"parameters":'
            '{"pagination_token":["saved-token"]},'
            '"message":"Invalid query operator"}]}'
        ),
        service="x",
        status_code=400,
    )
    matching_server_error = bot.ApiError(
        str(invalid_pagination_cursor_error()),
        service="x",
        status_code=503,
    )
    top_level_cursor_error = bot.ApiError(
        'X API error 400: {"message":"Invalid pagination token"}',
        service="x",
        status_code=400,
    )

    assert bot.api_error_is_invalid_pagination_cursor(
        invalid_pagination_cursor_error()
    ) is True
    assert bot.api_error_is_invalid_pagination_cursor(top_level_cursor_error) is True
    assert bot.api_error_is_invalid_pagination_cursor(unrelated_bad_request) is False
    assert bot.api_error_is_invalid_pagination_cursor(
        echoed_cursor_with_unrelated_error
    ) is False
    assert bot.api_error_is_invalid_pagination_cursor(matching_server_error) is False


def test_paginated_get_invalid_saved_cursor_retries_once_from_head() -> None:
    events: list[tuple[str, str | None]] = []

    def clear_invalid_cursor() -> None:
        events.append(("clear", None))

    def request(_path: str, params: dict) -> dict:
        token = params.get("pagination_token")
        events.append(("request", str(token) if token is not None else None))
        if token is not None:
            raise invalid_pagination_cursor_error()
        assert events[-2] == ("clear", None)
        assert params["since_id"] == "99"
        return {"data": [{"id": "100"}], "meta": {}}

    result = bot.x_paginated_get(
        request,
        "/2/users/12345/mentions",
        {
            "max_results": 10,
            "since_id": "99",
            "pagination_token": "expired-token",
        },
        max_pages=3,
        label="mentions",
        on_invalid_cursor=clear_invalid_cursor,
    )

    assert [item["id"] for item in result["data"]] == ["100"]
    assert result["_pagination"]["pages_fetched"] == 1
    assert result["_pagination"]["truncated"] is False
    assert result["_pagination"]["next_token"] is None
    assert result["_pagination"]["invalid_cursor_recovered"] is True
    assert events == [
        ("request", "expired-token"),
        ("clear", None),
        ("request", None),
    ]


def test_paginated_get_invalid_cursor_retry_is_bounded() -> None:
    events: list[tuple[str, str | None]] = []

    def clear_invalid_cursor() -> None:
        events.append(("clear", None))

    def request(_path: str, params: dict) -> dict:
        token = params.get("pagination_token")
        events.append(("request", str(token) if token is not None else None))
        raise invalid_pagination_cursor_error()

    with pytest.raises(bot.ApiError):
        bot.x_paginated_get(
            request,
            "/2/users/12345/mentions",
            {"pagination_token": "expired-token"},
            max_pages=3,
            label="mentions",
            on_invalid_cursor=clear_invalid_cursor,
        )

    assert events == [
        ("request", "expired-token"),
        ("clear", None),
        ("request", None),
    ]


def test_paginated_get_does_not_request_rejected_token_again_after_head_retry() -> None:
    events: list[tuple[str, str | None]] = []

    def request(_path: str, params: dict) -> dict:
        token = params.get("pagination_token")
        token_text = str(token) if token is not None else None
        events.append(("request", token_text))
        if token_text == "expired-token":
            raise invalid_pagination_cursor_error()
        return {
            "data": [{"id": "100"}],
            "meta": {"next_token": "expired-token"},
        }

    with pytest.raises(bot.PaginationCursorProtocolError, match="(?i)repeated"):
        bot.x_paginated_get(
            request,
            "/2/users/12345/mentions",
            {"pagination_token": "expired-token"},
            max_pages=3,
            label="mentions",
            on_invalid_cursor=lambda: events.append(("clear", None)),
        )

    assert events == [
        ("request", "expired-token"),
        ("clear", None),
        ("request", None),
    ]


def test_paginated_get_does_not_retry_unrelated_bad_request() -> None:
    events: list[str] = []
    unrelated_bad_request = bot.ApiError(
        'X API error 400: {"errors":[{"message":"Invalid query operator"}]}',
        service="x",
        status_code=400,
    )

    def request(_path: str, _params: dict) -> dict:
        events.append("request")
        raise unrelated_bad_request

    with pytest.raises(bot.ApiError) as raised:
        bot.x_paginated_get(
            request,
            "/2/users/12345/mentions",
            {"pagination_token": "saved-token"},
            max_pages=3,
            label="mentions",
            on_invalid_cursor=lambda: events.append("clear"),
        )

    assert raised.value is unrelated_bad_request
    assert events == ["request"]


def test_paginated_get_rejects_repeated_continuation_token_a_to_a() -> None:
    events: list[tuple[str, str | None]] = []

    def request(_path: str, params: dict) -> dict:
        token = params.get("pagination_token")
        token_text = str(token) if token is not None else None
        events.append(("request", token_text))
        return {
            "data": [{"id": "100" if token is None else "101"}],
            "meta": {"next_token": "A"},
        }

    with pytest.raises(bot.PaginationCursorProtocolError, match="(?i)repeated"):
        bot.x_paginated_get(
            request,
            "/2/users/12345/mentions",
            {},
            max_pages=5,
            label="mentions",
            on_invalid_cursor=lambda: events.append(("clear", None)),
        )

    assert events == [
        ("request", None),
        ("request", "A"),
        ("clear", None),
    ]


def test_paginated_get_rejects_repeated_continuation_token_a_to_b_to_a() -> None:
    events: list[tuple[str, str | None]] = []
    next_tokens = {
        None: "A",
        "A": "B",
        "B": "A",
    }

    def request(_path: str, params: dict) -> dict:
        token = params.get("pagination_token")
        token_text = str(token) if token is not None else None
        events.append(("request", token_text))
        return {
            "data": [{"id": str(100 + len(events))}],
            "meta": {"next_token": next_tokens[token_text]},
        }

    with pytest.raises(bot.PaginationCursorProtocolError, match="(?i)repeated"):
        bot.x_paginated_get(
            request,
            "/2/users/12345/mentions",
            {},
            max_pages=6,
            label="mentions",
            on_invalid_cursor=lambda: events.append(("clear", None)),
        )

    assert events == [
        ("request", None),
        ("request", "A"),
        ("request", "B"),
        ("clear", None),
    ]


def test_mentions_invalid_saved_cursor_clears_state_and_preserves_since_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "expired-token",
    }
    requests: list[dict] = []
    saved_cursors: list[dict] = []

    def request(_method: str, _path: str, *, params: dict) -> dict:
        requests.append(dict(params))
        if params.get("pagination_token"):
            raise invalid_pagination_cursor_error()
        return {"data": [], "meta": {}}

    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "x_request", request)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda current, **_kwargs: saved_cursors.append(
            copy.deepcopy(current.get("mention_pagination"))
        ),
    )

    assert bot.get_mentions(state) == []

    assert requests[0]["pagination_token"] == "expired-token"
    assert requests[0]["since_id"] == "99"
    assert "pagination_token" not in requests[1]
    assert requests[1]["since_id"] == "99"
    assert state["mention_pagination"] == {}
    assert saved_cursors[0] == {}


def test_mentions_invalid_cursor_stays_cleared_when_head_retry_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "expired-token",
    }
    requests: list[dict] = []
    saved_cursors: list[dict] = []

    def request(_method: str, _path: str, *, params: dict) -> dict:
        requests.append(dict(params))
        if params.get("pagination_token"):
            raise invalid_pagination_cursor_error()
        raise bot.ApiError(
            "X API error 503: temporary upstream failure",
            service="x",
            status_code=503,
        )

    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "x_request", request)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda current, **_kwargs: saved_cursors.append(
            copy.deepcopy(current.get("mention_pagination"))
        ),
    )

    with pytest.raises(bot.ApiError, match="temporary upstream failure"):
        bot.get_mentions(state)

    assert requests[0]["pagination_token"] == "expired-token"
    assert "pagination_token" not in requests[1]
    assert requests[1]["since_id"] == "99"
    assert state["mention_pagination"] == {}
    assert saved_cursors == [{}]


def test_hot_post_invalid_saved_cursor_clears_state_and_preserves_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["hot_post_reply_since_ids"] = {"700": "250"}
    state["hot_post_reply_pagination_tokens"] = {
        "700": "expired-token",
    }
    requests: list[dict] = []
    saved_token_maps: list[dict] = []

    def request(_path: str, params: dict) -> dict:
        requests.append(dict(params))
        if params.get("pagination_token"):
            raise invalid_pagination_cursor_error()
        return {"data": [], "meta": {}}

    monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", True)
    monkeypatch.setattr(bot, "HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS", 100)
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "load_extra_quote_watch_post_ids", lambda: ["700"])
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda current, **_kwargs: saved_token_maps.append(
            copy.deepcopy(current.get("hot_post_reply_pagination_tokens"))
        ),
    )

    assert bot.get_hot_post_reply_candidates(state) == []

    assert requests[0]["pagination_token"] == "expired-token"
    assert requests[0]["since_id"] == "250"
    assert requests[0]["query"] == requests[1]["query"]
    assert "pagination_token" not in requests[1]
    assert requests[1]["since_id"] == "250"
    assert state["hot_post_reply_pagination_tokens"] == {}
    assert saved_token_maps[0] == {}


def test_quote_lookup_invalid_saved_cursor_clears_state_and_retries_from_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["quote_lookup_pagination_tokens"] = {
        "900": "expired-token",
        "901": "other-token",
    }
    requests: list[dict] = []
    saved_token_maps: list[dict] = []

    def request(_path: str, params: dict) -> dict:
        requests.append(dict(params))
        if params.get("pagination_token") == "expired-token":
            raise invalid_pagination_cursor_error()
        return {"data": [], "meta": {}}

    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda current, **_kwargs: saved_token_maps.append(
            copy.deepcopy(current.get("quote_lookup_pagination_tokens"))
        ),
    )

    assert bot.get_quote_tweets_for_post("900", state) == []

    assert requests[0]["pagination_token"] == "expired-token"
    assert "pagination_token" not in requests[1]
    assert state["quote_lookup_pagination_tokens"] == {"901": "other-token"}
    assert saved_token_maps[0] == {"901": "other-token"}


def test_valid_tweets_sorted_by_id_deduplicates_paged_results() -> None:
    first = {"id": "20", "text": "same immutable post"}
    duplicate = {"id": "20", "text": "same immutable post"}

    result = bot.valid_tweets_sorted_by_id(
        [{"id": "30"}, first, {"id": "10"}, duplicate],
        context="paged test",
    )

    assert [item["id"] for item in result] == ["10", "20", "30"]
    assert result[1] is first


def test_pending_ai_reply_survives_state_round_trip_and_is_reused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    state = bot.default_state()
    context = unit_reply_context(target_id="100", contribution="A stable contribution.")
    reply = unit_approved_reply(context, text="The first draft remains the first draft.")
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True
    bot.save_state(state, durable=True)
    loaded = bot.load_state()
    reused = bot.pending_ai_reply(loaded, "100", "mention", context=context)
    assert reused == reply
    assert isinstance(reused, AIReply)
    assert reused.draft_record == reply.draft_record


def test_confirmed_reply_reconciliation_clears_pending_ai_draft() -> None:
    state = bot.default_state()
    context = unit_reply_context(target_id="100")
    reply = unit_approved_reply(context, text="A stable draft.")
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True
    receipt = unit_confirmed_reply_receipt(
        target_id="100",
        reply_post_id="900000",
        text="A stable draft.",
    )
    bot.apply_confirmed_reply_receipt(state, receipt)
    assert bot.pending_ai_reply(state, "100", "mention", context=context) is None


def test_nonempty_v1_pending_reply_draft_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "pending_reply_drafts": {
            "mention:100": {
                "target_id": "different", "candidate_source": "mention",
                "reply_text": "Unsafe stale draft.",
                "strategy_metadata": {"mode": "wry_reply", "reply_text": "Unsafe stale draft."},
            }
        }
    }
    state_file = tmp_path / "bot_state.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)

    with pytest.raises(RuntimeError, match="Legacy V1 reply drafts remain"):
        bot.load_state()


def test_pending_ai_reply_rejects_changed_incoming_context() -> None:
    state = bot.default_state()
    original = unit_reply_context(target_id="100", contribution="A first contribution.")
    changed = unit_reply_context(target_id="100", contribution="A materially different contribution.")
    reply = unit_approved_reply(original, text="Responsibility matters.", mode="opinion_or_principle")
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=original) is True

    assert bot.pending_ai_reply(state, "100", "mention", context=changed) is None


def test_safe_pending_opinion_reply_reuses_the_persisted_context() -> None:
    state = bot.default_state()
    incoming = "Institutions endure when people defend their purpose."
    text = "Institutions endure only when people defend their purpose."
    context = unit_reply_context(target_id="100", contribution=incoming)
    reply = unit_approved_reply(context, text=text, mode="opinion_or_principle")
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True

    assert bot.pending_ai_reply(state, "100", "mention", context=context) == reply


def test_pending_ai_reply_is_discarded_if_it_became_a_recent_duplicate() -> None:
    state = bot.default_state()
    incoming = "Institutions endure when people defend their purpose."
    text = "Institutions endure only when people defend their purpose."
    context = unit_reply_context(target_id="100", contribution=incoming)
    reply = unit_approved_reply(context, text=text, mode="opinion_or_principle")
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True

    assert bot.pending_ai_reply(
        state,
        "100",
        "mention",
        context=context,
        recent_replies=[text],
    ) is None
    assert "pending_ai_reply_drafts" not in state


def test_pending_ai_reply_rejects_overlong_incoming_context() -> None:
    state = bot.default_state()
    incoming = "Institutions endure when people defend their purpose. " + ("context " * 3000)
    text = "Institutions endure only when people defend their purpose."
    context = unit_reply_context(target_id="100", contribution=incoming)
    reply = unit_approved_reply(context, text=text, mode="opinion_or_principle")

    assert len(incoming) > 10_000
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is False


def test_pending_ai_reply_rejects_context_beyond_schema_limit() -> None:
    state = bot.default_state()
    text = "Institutions endure only when people defend their purpose."
    context = unit_reply_context(target_id="100", contribution="x" * 20_001)
    reply = unit_approved_reply(context, text=text, mode="opinion_or_principle")

    stored = bot.store_pending_ai_reply(state, "100", "mention", reply, context=context)

    assert stored is False
    assert not state.get("pending_ai_reply_drafts")


def test_pending_reply_created_under_an_older_strategy_version_is_not_reused() -> None:
    state = bot.default_state()
    context = unit_reply_context(target_id="100")
    reply = unit_approved_reply(context, text="An old draft.")
    record = dict(reply.draft_record)
    record["strategy_version"] = "ai-first-reply-v1"
    state["pending_ai_reply_drafts"] = {"mention:100": record}

    assert bot.pending_ai_reply(state, "100", "mention", context=context) is None


def test_pending_factual_reply_is_not_reused_when_evidence_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    context = unit_reply_context(target_id="100", contribution="What happened at the event?")
    reply = unit_approved_reply(
        context,
        text="People moved from East Germany towards West Germany in November 1989.",
        mode="direct_factual_answer",
        factual=True,
    )
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True
    missing_repository = UnitReplyEvidenceRepository()
    missing_repository.passages = {}
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: missing_repository)

    assert bot.pending_ai_reply(state, "100", "mention", context=context) is None


def test_pending_factual_reply_fails_closed_when_local_corpus_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    context = unit_reply_context(target_id="100", contribution="What happened at the event?")
    reply = unit_approved_reply(
        context,
        text="People moved from East Germany towards West Germany in November 1989.",
        mode="direct_factual_answer",
        factual=True,
    )
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True
    monkeypatch.setattr(
        bot,
        "reply_evidence_repository",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("invalid local corpus")),
    )

    assert bot.pending_ai_reply(state, "100", "mention", context=context) is None


def test_pending_reply_is_preserved_when_evidence_repository_is_temporarily_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    context = unit_reply_context(target_id="100")
    reply = unit_approved_reply(
        context,
        text="Institutions endure only when people defend their purpose.",
        mode="opinion_or_principle",
    )
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context)
    saved = copy.deepcopy(state["pending_ai_reply_drafts"]["mention:100"])
    monkeypatch.setattr(
        bot,
        "reply_evidence_repository",
        lambda: (_ for _ in ()).throw(bot.ReplyEvidenceUnavailable("corpus unavailable")),
    )

    with pytest.raises(bot.ReplyEvidenceUnavailable, match="corpus unavailable"):
        bot.pending_ai_reply(state, "100", "mention", context=context)

    assert state["pending_ai_reply_drafts"]["mention:100"] == saved


def test_pending_factual_reply_is_reused_after_source_hash_revalidation() -> None:
    state = bot.default_state()
    context = unit_reply_context(target_id="100", contribution="What happened at the event?")
    reply = unit_approved_reply(
        context,
        text="People moved from East Germany towards West Germany in November 1989.",
        mode="direct_factual_answer",
        factual=True,
    )
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True

    reused = bot.pending_ai_reply(state, "100", "mention", context=context)

    assert reused == reply
    assert reused.draft_record["source_hashes"] == reply.draft_record["source_hashes"]


def test_pending_ai_reply_drafts_are_bounded() -> None:
    state = bot.default_state()
    for target in range(101, 202):
        context = unit_reply_context(target_id=str(target))
        reply = unit_approved_reply(context, text="Stable draft.")
        assert bot.store_pending_ai_reply(state, str(target), "mention", reply, context=context)
    assert len(state["pending_ai_reply_drafts"]) == 100
    assert "mention:101" not in state["pending_ai_reply_drafts"]
    assert "mention:201" in state["pending_ai_reply_drafts"]


def test_confirmed_quote_tweet_reply_receipt_reconciliation_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))

    state = bot.default_state()
    state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")
    state["daily_quote_reply_date"] = state["daily_reply_date"]
    receipt = unit_confirmed_reply_receipt(
        target_id="910",
        reply_post_id="900000",
        author_id="310",
        lane="quote_tweet",
        epoch=fixed_epoch,
    )

    bot.write_confirmed_reply_receipt(receipt)
    assert bot.reconcile_confirmed_reply_receipt(state) is True
    bot.write_confirmed_reply_receipt(receipt)
    assert bot.reconcile_confirmed_reply_receipt(state) is True

    assert state["daily_reply_count"] == 1
    assert state["daily_quote_reply_count"] == 1
    assert state["daily_replied_author_counts"] == {"310": 1}
    assert state["replied_to_quote_post_ids"].count("910") == 1
    assert state["seen_quote_post_ids"].count("910") == 1
    assert state["own_auto_reply_ids"].count("900000") == 1


def test_confirmed_reply_receipt_persistence_failure_keeps_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
    receipt = unit_confirmed_reply_receipt(
        target_id="100",
        reply_post_id="900000",
        author_id="200",
        epoch=fixed_epoch,
    )
    state = bot.default_state()
    state["daily_reply_date"] = receipt["daily_reply_date"]

    bot.write_confirmed_reply_receipt(receipt)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: (_ for _ in ()).throw(OSError("state failed")))

    with pytest.raises(bot.ConfirmedReplyLocalPersistenceError):
        bot.reconcile_confirmed_reply_receipt(state)

    assert bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
    assert "100" in state["replied_to_ids"]


def test_confirmed_reply_normal_success_uses_durable_state_before_receipt_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(SCENARIOS / "normal_mention_reply.json")
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
        monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
        monkeypatch.setattr(
            bot,
            "generate_ai_first_reply",
            lambda context, *_args, **_kwargs: unit_approved_reply(context),
        )

        original_save_state = bot.save_state
        save_calls: list[bool] = []
        receipt_remove_seen = False

        def tracking_save_state(state: dict, **kwargs: object) -> None:
            save_calls.append(bool(kwargs.get("durable", False)))
            original_save_state(state, **kwargs)

        def tracking_remove_receipt(receipt: dict | None = None) -> None:
            nonlocal receipt_remove_seen
            assert save_calls and save_calls[-1] is True
            receipt_remove_seen = True
            bot.CONFIRMED_REPLY_RECEIPT_FILE.unlink()

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")
        monkeypatch.setattr(bot, "save_state", tracking_save_state)
        monkeypatch.setattr(bot, "remove_confirmed_reply_receipt", tracking_remove_receipt)

        assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_POSTED
        assert receipt_remove_seen is True
    finally:
        server.stop()


def test_confirmed_reply_latest_backup_recovers_suppression_after_primary_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(SCENARIOS / "normal_mention_reply.json")
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        state_file = tmp_path / "bot_state.json"
        monkeypatch.setattr(bot, "STATE_FILE", state_file)
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
        monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
        monkeypatch.setattr(
            bot,
            "generate_ai_first_reply",
            lambda context, *_args, **_kwargs: unit_approved_reply(context),
        )

        state = bot.default_state()
        state["daily_reply_date"] = bot.current_datetime().strftime("%Y-%m-%d")

        assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_POSTED
        assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
        latest_backup = json.loads((state_file.with_name("bot_state.json.bak1")).read_text(encoding="utf-8"))
        assert "100" in latest_backup["replied_to_ids"]

        state_file.write_text("{bad json", encoding="utf-8")
        recovered = bot.load_state()

        assert "100" in recovered["replied_to_ids"]
        assert recovered["last_seen_mention_id"] == "100"
        assert recovered["own_auto_reply_ids"] == ["900000"]
        assert recovered["daily_reply_count"] == 1
    finally:
        server.stop()


def test_malformed_confirmed_reply_receipt_blocks_mention_replies(tmp_path: Path) -> None:
    bot.CONFIRMED_REPLY_RECEIPT_FILE.write_text("{bad json", encoding="utf-8")
    state = bot.default_state()

    with pytest.raises(bot.InvalidConfirmedReplyReceipt):
        bot.reconcile_confirmed_reply_receipt(state)

    assert bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()


def test_confirmed_quote_tweet_reply_save_failure_replays_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    scenario["grok_replies"] = [
        "A point is useful only when it survives contact with reality. This one rather does.",
        "A point is useful only when it survives contact with reality. This one rather does.",
    ]
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        state_file = tmp_path / "bot_state.json"
        monkeypatch.setattr(bot, "STATE_FILE", state_file)
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
        monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
        monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_QUOTE_REPLIES_PER_DAY", 10)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "QUOTE_REPLY_DELAY_SECONDS", 0)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
        monkeypatch.setattr(
            bot,
            "generate_ai_first_reply",
            lambda context, *_args, **_kwargs: unit_approved_reply(
                context,
                text="A point is useful only when it survives contact with reality.",
                mode="opinion_or_principle",
            ),
        )

        original_save_state = bot.save_state
        initial_state = bot.default_state()
        initial_state.update(
            {
                "recent_own_post_ids": ["900"],
                "daily_reply_date": bot.current_datetime().strftime("%Y-%m-%d"),
                "daily_quote_reply_date": bot.current_datetime().strftime("%Y-%m-%d"),
                "daily_reply_count": 0,
                "daily_quote_reply_count": 0,
                "last_reply_epoch": 0,
                "replied_to_quote_post_ids": [],
                "seen_quote_post_ids": [],
                "daily_replied_author_ids": [],
                "daily_replied_author_counts": {},
                "own_auto_reply_ids": [],
                "tweet_cache": {},
            }
        )
        original_save_state(initial_state)

        def fail_first_post_success_save(state: dict, **kwargs: object) -> None:
            if server.posts:
                raise OSError("injected quote post-success save failure")
            original_save_state(state, **kwargs)

        monkeypatch.setattr(bot, "save_state", fail_first_post_success_save)
        first_state = bot.load_state()

        with pytest.raises(OSError, match="injected quote post-success save failure"):
            bot.maybe_reply_to_quote_tweets(first_state)

        assert len(server.posts) == 1
        first_reply_id = "900000"
        assert server.posts[0]["reply"]["in_reply_to_tweet_id"] == "910"

        durable_after_failed_save = json.loads(state_file.read_text(encoding="utf-8"))
        assert "910" not in durable_after_failed_save.get("replied_to_quote_post_ids", [])
        assert "910" not in durable_after_failed_save.get("seen_quote_post_ids", [])
        assert durable_after_failed_save.get("daily_reply_count") == 0
        assert durable_after_failed_save.get("daily_quote_reply_count") == 0
        assert durable_after_failed_save.get("last_reply_epoch") == 0
        assert durable_after_failed_save.get("daily_replied_author_counts", {}) == {}
        assert first_reply_id not in durable_after_failed_save.get("own_auto_reply_ids", [])

        monkeypatch.setattr(bot, "save_state", original_save_state)
        restarted_state = bot.load_state()
        second_status = bot.maybe_reply_to_quote_tweets(restarted_state)

        assert second_status == bot.QUOTE_CHECK_STATUS_CHECKED
        assert [post["reply"]["in_reply_to_tweet_id"] for post in server.posts] == ["910"]
    finally:
        server.stop()


def test_quote_tweet_receipt_reconciled_by_mention_lane_counts_quote_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    scenario["grok_replies"] = [
        "A point is useful only when it survives contact with reality. This one rather does.",
    ]
    server = FakeApiServer(scenario).start()
    try:
        fixed_epoch = 2_000_000_000
        reply_date = datetime.fromtimestamp(fixed_epoch).strftime("%Y-%m-%d")
        prior_date = "2033-05-17"
        state_file = tmp_path / "bot_state.json"
        monkeypatch.setattr(bot, "STATE_FILE", state_file)
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
        monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra_quote_watch_post_ids.txt")
        monkeypatch.setattr(bot, "X_BASE", server.url)
        monkeypatch.setattr(bot, "XAI_BASE", f"{server.url}/v1")
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
        monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
        monkeypatch.setattr(bot, "MARK_AI_REPLIES_AS_AI", False)
        monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
        monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
        monkeypatch.setattr(bot, "MAX_QUOTE_REPLIES_PER_DAY", 10)
        monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
        monkeypatch.setattr(bot, "QUOTE_REPLY_DELAY_SECONDS", 0)
        monkeypatch.setattr(bot, "MY_USER_ID", "12345")
        monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
        monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(fixed_epoch))
        monkeypatch.setattr(
            bot,
            "generate_ai_first_reply",
            lambda context, *_args, **_kwargs: unit_approved_reply(
                context,
                text="A point is useful only when it survives contact with reality.",
                mode="opinion_or_principle",
            ),
        )

        original_save_state = bot.save_state
        initial_state = bot.default_state()
        initial_state.update(
            {
                "recent_own_post_ids": ["900"],
                "daily_reply_date": reply_date,
                "daily_quote_reply_date": prior_date,
                "daily_reply_count": 0,
                "daily_quote_reply_count": 0,
                "last_reply_epoch": 0,
                "replied_to_ids": [],
                "replied_to_quote_post_ids": [],
                "seen_quote_post_ids": [],
                "daily_replied_author_ids": [],
                "daily_replied_author_counts": {},
                "own_auto_reply_ids": [],
                "tweet_cache": {},
            }
        )
        original_save_state(initial_state)

        def fail_first_post_success_save(state: dict, **kwargs: object) -> None:
            if server.posts:
                raise OSError("injected quote post-success save failure")
            original_save_state(state, **kwargs)

        monkeypatch.setattr(bot, "save_state", fail_first_post_success_save)
        first_state = bot.load_state()

        with pytest.raises(OSError, match="injected quote post-success save failure"):
            bot.maybe_reply_to_quote_tweets(first_state)

        assert [post["reply"]["in_reply_to_tweet_id"] for post in server.posts] == ["910"]
        durable_after_failed_save = json.loads(state_file.read_text(encoding="utf-8"))
        assert durable_after_failed_save["daily_quote_reply_date"] == reply_date
        assert durable_after_failed_save["daily_quote_reply_count"] == 0
        assert bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()

        monkeypatch.setattr(bot, "save_state", original_save_state)
        restarted_state = bot.load_state()

        assert bot.maybe_reply_to_mentions(restarted_state) == bot.NORMAL_CHECK_STATUS_CHECKED
        assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
        assert restarted_state["daily_quote_reply_date"] == reply_date
        assert restarted_state["daily_quote_reply_count"] == 1

        bot.reset_daily_quote_reply_count_if_needed(restarted_state)
        assert restarted_state["daily_quote_reply_date"] == reply_date
        assert restarted_state["daily_quote_reply_count"] == 1
        assert [post["reply"]["in_reply_to_tweet_id"] for post in server.posts] == ["910"]
    finally:
        server.stop()


def test_cache_tweet_uses_fake_clock_for_generated_created_at(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_epoch = 1_800_000_000
    state = bot.default_state()
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)

    cached = bot.cache_tweet(
        state,
        tweet_id="123",
        text="hello",
        author_id="456",
    )

    assert cached["cached_epoch"] == fixed_epoch
    assert cached["created_at"] == datetime.fromtimestamp(fixed_epoch).isoformat()


def test_cache_tweet_normalises_safe_scalar_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_epoch = 1_800_000_000
    state = bot.default_state()
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)

    cached = bot.cache_tweet(
        state,
        tweet_id=123,
        text=["not", "a", "string"],
        author_id=456,
        conversation_id=789,
        referenced_tweets=[{"type": 1, "id": 2}],
        created_at=111,
        image_summary=222,
        post_type=333,
    )

    assert cached == {
        "id": "123",
        "author_id": "456",
        "conversation_id": "789",
        "created_at": "111",
        "referenced_tweets": [{"type": "1", "id": "2"}],
        "text": "['not', 'a', 'string']",
        "cached_epoch": fixed_epoch,
        "image_summary": "222",
        "post_type": "333",
    }


@pytest.mark.parametrize("referenced_tweets", ["banana", ["banana"]])
def test_cache_tweet_rejects_malformed_referenced_tweets(referenced_tweets: object) -> None:
    state = bot.default_state()

    with pytest.raises(ValueError):
        bot.cache_tweet(
            state,
            tweet_id="123",
            text="hello",
            author_id="456",
            referenced_tweets=referenced_tweets,
        )

    assert state["tweet_cache"] == {}


def test_get_immediate_parent_id_accepts_absent_or_valid_references() -> None:
    assert bot.get_immediate_parent_id({}) is None
    assert bot.get_immediate_parent_id({"referenced_tweets": None}) is None
    assert bot.get_immediate_parent_id({
        "referenced_tweets": [{"type": "quoted", "id": "111"},
                              {"type": "replied_to", "id": 222}],
    }) == "222"


@pytest.mark.parametrize(
    "referenced_tweets",
    ["banana", ["banana"], [{"type": "replied_to"}],
     [{"type": "replied_to", "id": "not-a-tweet-id"}]],
)
def test_get_immediate_parent_id_rejects_malformed_api_references(
    referenced_tweets: object,
) -> None:
    with pytest.raises(bot.ApiError, match="malformed referenced_tweets"):
        bot.get_immediate_parent_id({"referenced_tweets": referenced_tweets})


def test_load_state_rejects_malformed_last_seen_mention_id_and_recovers_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    bot.atomic_write_json(state_file, {"last_seen_mention_id": []})
    bot.atomic_write_json(tmp_path / "bot_state.json.bak1", {"last_seen_mention_id": "123"})

    recovered = bot.load_state()

    assert recovered["last_seen_mention_id"] == "123"


def test_load_state_normalises_optional_scalar_ids(tmp_path: Path) -> None:
    state = {
        "last_seen_mention_id": 123,
        "last_main_post_id": 456,
        "last_regular_image_filename": 789,
    }

    normalised = bot.normalise_state_candidate(state, path=tmp_path / "bot_state.json")

    assert normalised is not None
    assert normalised["last_seen_mention_id"] == "123"
    assert normalised["last_main_post_id"] == "456"
    assert normalised["last_regular_image_filename"] == "789"


def test_mention_pagination_state_canonicalises_legacy_empty_base(
    tmp_path: Path,
) -> None:
    normalised = bot.normalise_mention_pagination(
        {"next_token": "page-4"},
        path=tmp_path / "bot_state.json",
    )

    assert normalised == {
        "base_since_id": "",
        "next_token": "page-4",
    }


@pytest.mark.parametrize(
    "value",
    [
        {"base_since_id": "99"},
        {"base_since_id": [], "next_token": "page-4"},
        {"base_since_id": "99", "next_token": {"nested": "bad"}},
        {
            "base_since_id": "99",
            "next_token": "page-4",
            "unexpected": True,
        },
    ],
)
def test_mention_pagination_state_rejects_malformed_cursor(
    tmp_path: Path,
    value: object,
) -> None:
    assert bot.normalise_mention_pagination(
        value,
        path=tmp_path / "bot_state.json",
    ) is None


def test_quote_tweet_missing_created_at_is_not_old_enough() -> None:
    assert bot.quote_tweet_is_old_enough({"id": "123"}) is False
    assert bot.quote_tweet_is_old_enough({"id": "123", "created_at": "not a date"}) is False


@pytest.mark.parametrize(
    "state",
    [
        {
            "next_meme_post_epoch": 1_800_010_000,
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_schedule_mode": "delayed_recent_quote",
            "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
            "meme_anchor_quote_post_epoch": 1_800_000_000,
        },
        {
            "next_meme_post_epoch": 1_800_010_000,
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "next_meme_schedule_date": bot.epoch_date_str(1_800_000_000),
            "meme_anchor_quote_post_epoch": 0,
        },
        {
            "next_meme_post_epoch": 1_800_000_000,
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "next_meme_schedule_date": bot.epoch_date_str(1_800_000_000),
            "meme_anchor_quote_post_epoch": 1_800_000_000,
        },
        {
            "next_meme_post_epoch": 1_800_010_000,
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": "2000-01-01",
            "meme_anchor_quote_post_epoch": 0,
        },
    ],
)
def test_normalise_state_rejects_inconsistent_meme_schedule(state: dict, tmp_path: Path) -> None:
    assert bot.normalise_state_candidate(state, path=tmp_path / "bot_state.json") is None


def test_old_meme_schedule_version_survives_load_until_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "bot_state.json"
    old_state = {
        "meme_schedule_version": 1,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    bot.atomic_write_json(state_file, old_state)

    loaded = bot.load_state()

    assert loaded["meme_schedule_version"] == 1
    assert loaded["next_meme_post_epoch"] == 1_800_010_000


def test_ensure_meme_schedule_initialized_migrates_old_version(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {
        "meme_schedule_version": 1,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    saved: list[dict] = []
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: saved.append(json.loads(json.dumps(state))))

    bot.ensure_meme_schedule_initialized(state)

    assert state["meme_schedule_version"] == bot.MEME_SCHEDULE_VERSION
    assert state["next_meme_schedule_mode"] == "fallback_migrated"
    assert state["meme_anchor_quote_post_epoch"] == 0
    assert bot.validate_meme_schedule_state(state, path=Path("state.json"))
    assert saved


def test_future_meme_schedule_version_is_rejected(tmp_path: Path) -> None:
    state = {
        "meme_schedule_version": 999,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }

    assert bot.normalise_state_candidate(state, path=tmp_path / "bot_state.json") is None


def test_current_meme_schedule_version_valid_state_is_unchanged(tmp_path: Path) -> None:
    state = {
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }

    normalised = bot.normalise_state_candidate(state, path=tmp_path / "bot_state.json")

    assert normalised is not None
    for key, value in state.items():
        assert normalised[key] == value


def test_current_active_meme_schedule_must_be_receipt_compatible(tmp_path: Path) -> None:
    bad_state = {
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "next_meme_post_epoch": 100,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(100),
        "meme_anchor_quote_post_epoch": 0,
    }
    assert bot.normalise_state_candidate(bad_state, path=tmp_path / "bot_state.json") is None

    good_state = {
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    normalised = bot.normalise_state_candidate(good_state, path=tmp_path / "bot_state.json")
    assert normalised is not None

    receipt = valid_regular_receipt(
        next_meme_post_epoch=normalised["next_meme_post_epoch"],
        next_meme_schedule_mode=normalised["next_meme_schedule_mode"],
        next_meme_schedule_date=normalised["next_meme_schedule_date"],
        meme_anchor_quote_post_epoch=normalised["meme_anchor_quote_post_epoch"],
        meme_schedule_changed_by_quote=False,
    )
    assert bot.regular_post_receipt_is_semantically_valid(receipt)


def test_too_old_current_active_meme_schedule_recovers_from_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    bot.atomic_write_json(
        state_file,
        {
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_post_epoch": 100,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": bot.epoch_date_str(100),
            "meme_anchor_quote_post_epoch": 0,
        },
    )
    bot.atomic_write_json(
        tmp_path / "bot_state.json.bak1",
        {
            "replied_to_ids": ["from-bak1"],
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_post_epoch": 1_800_010_000,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
            "meme_anchor_quote_post_epoch": 0,
        },
    )

    recovered = bot.load_state()

    assert recovered["replied_to_ids"] == ["from-bak1"]
    assert recovered["next_meme_post_epoch"] == 1_800_010_000


def test_unresolved_meme_receipt_blocks_another_main_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.atomic_write_json(
        bot.MEME_POST_RECEIPT_FILE,
        {
            "schema_version": 1,
            "post_id": "970001",
            "meme_basename": "001_meme.png",
            "meme_post_epoch": 1_800_000_000,
            "next_meme_post_epoch": 1_800_086_400,
        },
    )

    bot.post_random_quote(lines_used, images_used, state)
    assert state["last_main_post_id"] == "950001"


def test_simultaneous_regular_and_meme_receipts_block_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.atomic_write_json(
        receipt_file,
        {
            "schema_version": 1,
            "post_id": "950001",
            "quote_hash": bot.quote_text_hash("Good quote."),
            "image_basename": "t01.jpg",
            "quote_post_epoch": 1_800_000_000,
            "next_quote_post_epoch": 1_800_007_200,
            "text": "Good quote.",
        },
    )
    bot.atomic_write_json(
        bot.MEME_POST_RECEIPT_FILE,
        {
            "schema_version": 1,
            "post_id": "970001",
            "meme_basename": "001_meme.png",
            "meme_post_epoch": 1_800_000_000,
            "next_meme_post_epoch": 1_800_086_400,
        },
    )

    with pytest.raises(bot.InvalidRegularPostReceipt):
        bot.reconcile_main_post_receipts(lines_used, images_used, state)


def test_simultaneous_legacy_confirmed_receipts_block_auxiliary_provider_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regular = valid_regular_receipt()
    meme = {
        "schema_version": 1,
        "post_id": "970001",
        "meme_basename": "001_meme.png",
        "meme_post_epoch": 1_800_000_000,
        "next_meme_post_epoch": 1_800_086_400,
    }
    reply = unit_confirmed_reply_receipt()
    bot.atomic_write_json(bot.REGULAR_POST_RECEIPT_FILE, regular, durable=True)
    bot.atomic_write_json(bot.MEME_POST_RECEIPT_FILE, meme, durable=True)
    bot.atomic_write_json(bot.CONFIRMED_REPLY_RECEIPT_FILE, reply, durable=True)
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: pytest.fail(
            "unresolved legacy receipts must block auxiliary transport"
        ),
    )

    assert bot.load_regular_post_receipt()[0] == "valid"
    assert bot.load_meme_post_receipt()[0] == "valid"
    assert bot.load_confirmed_reply_receipt()[0] == "valid"
    assert bot.ambiguous_remote_post_is_blocking() is True
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.require_remote_operation_unpaused("auxiliary provider request")


def test_fully_reconciled_receipt_namespace_does_not_create_false_barrier() -> None:
    assert bot.load_regular_post_receipt() == ("absent", None)
    assert bot.load_meme_post_receipt() == ("absent", None)
    assert bot.load_confirmed_reply_receipt() == ("absent", None)
    assert bot.ambiguous_remote_post_is_blocking() is False


def test_post_next_meme_rejects_simultaneous_receipts_without_removing_either(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _lines_used, _images_used, _state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.atomic_write_json(
        receipt_file,
        {
            "schema_version": 1,
            "post_id": "950001",
            "quote_hash": bot.quote_text_hash("Good quote."),
            "image_basename": "t01.jpg",
            "quote_post_epoch": 1_800_000_000,
            "next_quote_post_epoch": 1_800_007_200,
            "text": "Good quote.",
        },
    )
    bot.atomic_write_json(
        bot.MEME_POST_RECEIPT_FILE,
        {
            "schema_version": 1,
            "post_id": "970001",
            "meme_basename": "001_meme.png",
            "meme_post_epoch": 1_800_000_000,
            "next_meme_post_epoch": 1_800_086_400,
        },
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_next_meme({})

    assert receipt_file.exists()
    assert bot.MEME_POST_RECEIPT_FILE.exists()


def test_invalid_meme_receipt_blocks_main_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.MEME_POST_RECEIPT_FILE.write_text("{bad json", encoding="utf-8")

    with pytest.raises(bot.InvalidMemePostReceipt):
        bot.post_random_quote(lines_used, images_used, state)


@pytest.mark.parametrize("next_epoch", [1_800_000_000, 1_799_999_999])
def test_meme_receipt_requires_future_next_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    next_epoch: int,
) -> None:
    receipt_file = tmp_path / "meme_post_receipt.json"
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", receipt_file)
    bot.atomic_write_json(
        receipt_file,
        {
            "schema_version": 1,
            "post_id": "970001",
            "meme_basename": "001_meme.png",
            "meme_post_epoch": 1_800_000_000,
            "next_meme_post_epoch": next_epoch,
        },
    )

    with pytest.raises(bot.InvalidMemePostReceipt):
        bot.reconcile_meme_post_receipt({})


def test_state_backup_bak1_contains_newest_committed_meme_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 5)
    bot.save_state({"posted_meme_filenames": [], "next_meme_post_epoch": 1_700_000_000}, durable=True)
    committed = {
        "posted_meme_filenames": ["001_meme.png"],
        "last_main_post_id": "970001",
        "last_meme_post_epoch": 1_800_000_000,
        "next_meme_post_epoch": 1_800_086_400,
    }
    bot.save_state(committed, durable=True)
    state_file.write_text("{bad json", encoding="utf-8")

    recovered = bot.load_state()

    assert recovered["posted_meme_filenames"] == ["001_meme.png"]
    assert recovered["last_main_post_id"] == "970001"
    assert recovered["next_meme_post_epoch"] == 1_800_086_400


def test_state_backup_bak1_contains_newest_committed_regular_quote_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 5)
    bot.save_state({"next_quote_post_epoch": 1}, durable=True)
    committed = {
        "last_main_post_id": "950001",
        "last_quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
    }
    bot.save_state(committed, durable=True)
    state_file.write_text("{bad json", encoding="utf-8")

    recovered = bot.load_state()

    assert recovered["last_main_post_id"] == "950001"
    assert recovered["last_quote_post_epoch"] == 1_800_000_000
    assert recovered["next_quote_post_epoch"] == 1_800_007_200


def test_protected_durable_write_fails_when_parent_fsync_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "protected.json"
    real_open = bot.os.open

    def failing_open(path: str, flags: int, mode: int = 0o777) -> int:
        if Path(path) == tmp_path:
            raise OSError("directory fsync unavailable")
        return real_open(path, flags, mode)

    monkeypatch.setattr(bot.os, "open", failing_open)

    with pytest.raises(OSError, match="directory fsync unavailable"):
        bot.atomic_write_json(target, {"ok": True}, durable=True)


def test_missing_per_image_analysis_is_excluded_with_valid_alternative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    missing_path = image_dir / "t01.jpg"
    valid_path = image_dir / "t02.jpg"
    missing_path.write_bytes(b"missing")
    valid_path.write_bytes(b"valid")
    analysis = image_analysis_for_paths([missing_path, valid_path])
    missing_hash = bot.file_sha256(missing_path)
    analysis["items"].pop(missing_hash)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)

    chosen = bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["anything"]}}, {})

    assert chosen["basename"] == "t02.jpg"


def test_image_hash_failure_excludes_image_with_valid_alternative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    bad_path = image_dir / "t01.jpg"
    good_path = image_dir / "t02.jpg"
    bad_path.write_bytes(b"bad")
    good_path.write_bytes(b"good")
    analysis = image_analysis_for_paths([bad_path, good_path])
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)
    real_hash = bot.current_image_sha256

    def maybe_fail(path: str) -> str:
        if path.endswith("t01.jpg"):
            raise OSError("read failed")
        return real_hash(path)

    monkeypatch.setattr(bot, "current_image_sha256", maybe_fail)

    chosen = bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["anything"]}}, {})

    assert chosen["basename"] == "t02.jpg"


def test_all_image_hash_failures_raise_global_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"bad")
    analysis = image_analysis_for_paths([image_path])
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)
    monkeypatch.setattr(bot, "current_image_sha256", lambda path, **_kwargs: (_ for _ in ()).throw(OSError("read failed")))

    with pytest.raises(bot.GlobalImageUnavailable):
        bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["anything"]}}, {})


def test_invalid_per_image_analysis_type_is_excluded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"invalid")
    analysis = image_analysis_for_paths([image_path])
    image_hash = bot.file_sha256(image_path)
    analysis["items"][image_hash]["analysis"] = "bad"
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: analysis)

    with pytest.raises(bot.GlobalImageUnavailable):
        bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["anything"]}}, {})


def test_global_image_failure_is_not_retried_across_quotes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_quote(lines_used: set, *, excluded_quote_hashes: set[str] | None = None) -> dict:
        nonlocal calls
        calls += 1
        return {"line_no": calls - 1, "quote_hash": f"{calls:064x}", "text": f"Quote {calls}.", "analysis": {}}

    monkeypatch.setattr(bot, "reconcile_main_post_receipts", lambda *args, **kwargs: {"regular": False, "meme": False})
    monkeypatch.setattr(bot, "quote_used_history_has_legacy_indices", lambda used: False)
    monkeypatch.setattr(bot, "choose_unused_line_candidate", fake_quote)
    monkeypatch.setattr(bot, "choose_matched_unused_image", lambda *args, **kwargs: (_ for _ in ()).throw(bot.GlobalImageUnavailable("no corpus")))

    with pytest.raises(bot.GlobalImageUnavailable):
        bot.post_random_quote(set(), set(), {})

    assert calls == 1


def test_image_cycle_status_log_formats_without_argument_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"valid")
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "load_image_analysis", lambda: image_analysis_for_paths([image_path]))

    bot.choose_matched_unused_image(set(), {"analysis": {"primary_topics": ["anything"]}}, {})

    assert "Image cycle status:" in caplog.text


def test_append_unique_capped_preserves_order_and_moves_existing_item_to_tail() -> None:
    assert bot.append_unique_capped(["a", "b", "c"], "b", 3) == ["a", "c", "b"]


def test_append_unique_capped_discards_oldest_items() -> None:
    assert bot.append_unique_capped(["a", "b", "c"], "d", 3) == ["b", "c", "d"]


def test_completed_reply_target_ledger_never_evicts_old_ids() -> None:
    existing = ["oldest", *(str(index) for index in range(2500))]
    updated = bot.append_unique_durable(existing, "newest")

    assert updated[0] == "oldest"
    assert updated[-1] == "newest"
    assert len(updated) == 2502
    assert bot.append_unique_durable(updated, "oldest") == updated


def test_quote_tweet_completed_ledger_is_not_a_bounded_seen_cache() -> None:
    state = bot.default_state()
    state["replied_to_quote_post_ids"] = ["oldest", *(str(index) for index in range(2500))]

    bot.mark_quote_tweet_replied(state, "newest")

    assert "oldest" in state["replied_to_quote_post_ids"]
    assert state["replied_to_quote_post_ids"][-1] == "newest"
    assert len(state["replied_to_quote_post_ids"]) == 2502


def reply_evaluation_record(target_id: str, evaluated_epoch: int) -> dict:
    return {
        "target_id": target_id,
        "lane": "mention",
        "outcome": "no_reply",
        "reason": "terminal",
        "evaluated_epoch": evaluated_epoch,
    }


def test_recent_reply_evaluations_survive_nominal_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MAX_RECORDS", 2)
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MIN_RETENTION_SECONDS", 100)
    warnings: list[str] = []
    monkeypatch.setattr(
        bot.log,
        "warning",
        lambda message, *args: warnings.append(message % args),
    )
    state = bot.default_state()
    state["reply_evaluation_records"] = {
        target_id: reply_evaluation_record(target_id, epoch)
        for target_id, epoch in (("a", 950), ("b", 960), ("c", 970))
    }

    bot.prune_reply_evaluation_records(state, current_epoch=1000)

    assert list(state["reply_evaluation_records"]) == ["a", "b", "c"]
    assert any("retaining all protected records" in warning for warning in warnings)


def test_old_reply_evaluation_overflow_prunes_oldest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MAX_RECORDS", 3)
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MIN_RETENTION_SECONDS", 100)
    state = {
        "reply_evaluation_records": {
            target_id: reply_evaluation_record(target_id, epoch)
            for target_id, epoch in (("e", 5), ("a", 1), ("d", 4), ("b", 2), ("c", 3))
        }
    }

    bot.prune_reply_evaluation_records(state, current_epoch=1000)

    assert list(state["reply_evaluation_records"]) == ["c", "d", "e"]


def test_reply_evaluation_ties_are_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MAX_RECORDS", 2)
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MIN_RETENTION_SECONDS", 100)
    first = {
        "reply_evaluation_records": {
            target_id: reply_evaluation_record(target_id, 1)
            for target_id in ("c", "a", "b")
        }
    }
    second = {
        "reply_evaluation_records": {
            target_id: reply_evaluation_record(target_id, 1)
            for target_id in ("b", "c", "a")
        }
    }

    bot.prune_reply_evaluation_records(first, current_epoch=1000)
    bot.prune_reply_evaluation_records(second, current_epoch=1000)

    assert list(first["reply_evaluation_records"]) == ["b", "c"]
    assert first["reply_evaluation_records"] == second["reply_evaluation_records"]


def test_recorded_terminal_reply_evaluation_remains_replay_protection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MAX_RECORDS", 2)
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MIN_RETENTION_SECONDS", 100)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1000)
    state = {
        "reply_evaluation_records": {
            target_id: reply_evaluation_record(target_id, epoch)
            for target_id, epoch in (("oldest", 1), ("older", 2))
        }
    }
    bot.record_terminal_reply_evaluation(
        state,
        target_id="newest",
        lane="mention",
        reason="terminal",
    )

    assert bot.terminal_reply_evaluation(state, "oldest") is None
    assert bot.terminal_reply_evaluation(state, "newest") is not None
    assert list(state["reply_evaluation_records"]) == ["older", "newest"]


def test_normalised_loaded_state_prunes_oversized_reply_evaluations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MAX_RECORDS", 2)
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MIN_RETENTION_SECONDS", 100)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1000)
    state = {
        "reply_evaluation_records": {
            target_id: reply_evaluation_record(target_id, epoch)
            for target_id, epoch in (("one", 1), ("three", 3), ("two", 2))
        }
    }

    normalised = bot.normalise_state_candidate(state, path=tmp_path / "bot_state.json")

    assert normalised is not None
    assert list(normalised["reply_evaluation_records"]) == ["two", "three"]


def test_log_json_debug_recursively_redacts_credentials_and_keeps_metadata() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    previous_level = bot.log.level
    bot.log.addHandler(handler)
    bot.log.setLevel(logging.DEBUG)
    try:
        bot.log_json_debug(
            "payload",
            {
                "request_id": "request-123",
                "nested": {
                    "API-Key": "api-key-value",
                    "Authorization": "Bearer auth-value",
                    "items": [
                        {"oauth_token": "oauth-value", "status": "harmless"},
                        {"cookieJar": "cookie-value", "count": 3},
                    ],
                },
            },
        )
    finally:
        bot.log.removeHandler(handler)
        bot.log.setLevel(previous_level)

    output = stream.getvalue()
    assert "api-key-value" not in output
    assert "auth-value" not in output
    assert "oauth-value" not in output
    assert "cookie-value" not in output
    assert output.count("[REDACTED]") == 4
    assert "request-123" in output
    assert "harmless" in output
    assert '"count": 3' in output


def test_save_state_debug_logging_uses_value_free_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    state = bot.default_state()
    state["tweet_cache"] = {"10": {"text": "cached incoming post secret text"}}
    state["pending_ai_reply_drafts"] = {
        "10": {"proposed_reply": "private reply draft text"}
    }
    state["provider_credentials"] = {"api_key": "credential-value"}
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    previous_level = bot.log.level
    bot.log.addHandler(handler)
    bot.log.setLevel(logging.DEBUG)
    try:
        bot.save_state(state)
    finally:
        bot.log.removeHandler(handler)
        bot.log.setLevel(previous_level)

    output = stream.getvalue()
    assert "State summary being saved" in output
    assert "tweet_cache" in output
    assert "pending_ai_reply_drafts" in output
    assert "cached incoming post secret text" not in output
    assert "private reply draft text" not in output
    assert "credential-value" not in output


def test_logging_defaults_to_info_and_explicit_debug_remains_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    assert bot.setup_logging(configure_file_logging=False).level == logging.INFO

    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    debug_logger = bot.setup_logging(configure_file_logging=False)
    assert debug_logger.level == logging.DEBUG
    assert debug_logger.isEnabledFor(logging.DEBUG)

    monkeypatch.delenv("LOG_LEVEL", raising=False)
    bot.setup_logging(configure_file_logging=False)


def test_reply_daily_cap_dates_ignore_ambient_timezone_and_reset_authors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    epoch = int(datetime(2026, 7, 1, 0, 30, tzinfo=ZoneInfo("Europe/London")).timestamp())
    original_tz = os.environ.get("TZ")
    try:
        for ambient_tz in ("UTC", "America/Los_Angeles"):
            os.environ["TZ"] = ambient_tz
            time.tzset()
            monkeypatch.setattr(bot, "now_epoch", lambda: epoch)
            assert bot.reply_cap_date_str() == "2026-07-01"
            state = bot.default_state()
            state.update(
                {
                    "daily_reply_date": "2026-06-30",
                    "daily_reply_count": 4,
                    "daily_replied_author_ids": ["42"],
                    "daily_replied_author_counts": {"42": 2},
                    "daily_quote_reply_date": "2026-06-30",
                    "daily_quote_reply_count": 3,
                }
            )
            bot.reset_daily_reply_count_if_needed(state)
            bot.reset_daily_quote_reply_count_if_needed(state)
            assert state["daily_reply_date"] == "2026-07-01"
            assert state["daily_quote_reply_date"] == "2026-07-01"
            assert state["daily_replied_author_ids"] == []
            assert state["daily_replied_author_counts"] == {}
            assert state["daily_reply_count"] == 0
            assert state["daily_quote_reply_count"] == 0
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
def test_xai_structured_reply_transport_preserves_separated_context(
    lane: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account_post = (
        "I know some of the images have been a bit odd of late. "
        "I'm working on it - bear with me..."
    )
    incoming = "Not her most memorable quote"
    context = unit_reply_context(target_id="100", lane=lane, contribution=incoming)
    context["quoted_post"] = {
        "post_id": "90",
        "author_role": "account",
        "text": account_post,
    }

    requests_seen: list[dict] = []

    def fake_post(*args: object, **kwargs: object) -> bot.requests.Response:
        requests_seen.append(kwargs["json"])
        return fake_xai_response(200, {"choices": [{"message": {"content": {"mode": "no_reply"}}}]})

    monkeypatch.setattr(bot.requests, "post", fake_post)

    result = bot.xai_structured_reply_call(
        stage="proposer",
        model="unit-model",
        system_prompt="system policy",
        user_prompt=json.dumps(context, sort_keys=True),
        response_schema={"type": "object", "additionalProperties": False},
        timeout_seconds=5,
        max_output_tokens=100,
        media_context=None,
    )
    assert result == {"mode": "no_reply"}
    assert len(requests_seen) == 1
    payload = requests_seen[0]
    assert payload["model"] == "unit-model"
    assert [message["role"] for message in payload["messages"]] == ["system", "user"]
    prompt = payload["messages"][1]["content"]
    assert isinstance(prompt, str)
    assert account_post in prompt
    assert incoming in prompt
    assert payload["response_format"]["json_schema"]["strict"] is True


def test_unavailable_media_instruction_preserves_structured_schema() -> None:
    prompt = bot.xai_user_content(
        "structured prompt",
        {"status": "unavailable", "photos_expected": 1},
    )
    assert isinstance(prompt, str)
    assert "Return exactly SKIP" not in prompt
    assert "proposer mode=no_reply" in prompt
    assert "reviewer verdict=reject" in prompt


def test_long_parent_context_never_truncates_away_incoming_contribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incoming = "INCOMING-ISSUE-MARKER responsibility for local government"
    mention = {"id": "900", "text": incoming, "conversation_id": "700"}
    chain = [
        {
            "id": str(index),
            "author_id": str(100 + index),
            "text": f"parent-{index} " + ("inherited context " * 80),
        }
        for index in range(1, 6)
    ]
    monkeypatch.setattr(bot, "ALWAYS_FETCH_PARENT_FOR_CONTEXT", True)
    monkeypatch.setattr(bot, "SKIP_REPLIES_TO_OWN_AUTO_REPLIES", False)
    monkeypatch.setattr(bot, "build_parent_chain", lambda _mention, _state: chain)

    context, should_continue = bot.build_context_for_reply_ai(mention, bot.default_state())

    assert should_continue is True
    assert context["incoming_contribution"] == incoming
    assert len(context["parent_thread"]) == 3
    assert context["parent_thread"][0]["post_id"] == "3"
    assert context["parent_thread"][-1]["post_id"] == "5"
    assert sum(len(parent["text"]) for parent in context["parent_thread"]) <= bot.THREAD_CONTEXT_MAX_TOTAL_CHARS
    assert all(
        len(parent["text"]) <= bot.THREAD_CONTEXT_MAX_CHARS_PER_POST
        for parent in context["parent_thread"]
    )


def test_author_cap_context_merges_siblings_with_parent_dedup_and_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_epoch = bot.now_epoch()
    mention = {
        "id": "300",
        "author_id": "200",
        "conversation_id": "700",
        "text": "What follows from all that?",
        "referenced_tweets": [{"type": "replied_to", "id": "150"}],
    }
    state = bot.default_state()
    state["tweet_cache"] = {
        "100": {
            "id": "100", "cached_epoch": cache_epoch, "author_id": "12345", "conversation_id": "700",
            "text": "Opening post.", "referenced_tweets": [],
        },
        "150": {
            "id": "150", "cached_epoch": cache_epoch, "author_id": "200", "conversation_id": "700",
            "text": "Immediate capped parent.", "post_type": "author_cap_context",
            "referenced_tweets": [{"type": "replied_to", "id": "100"}],
        },
        "160": {
            "id": "160", "cached_epoch": cache_epoch, "author_id": "200", "conversation_id": "700",
            "text": "Older capped sibling.", "post_type": "author_cap_context",
            "referenced_tweets": [{"type": "replied_to", "id": "100"}],
        },
        "170": {
            "id": "170", "cached_epoch": cache_epoch, "author_id": "200", "conversation_id": "700",
            "text": "Newer capped sibling.", "post_type": "author_cap_context",
            "referenced_tweets": [{"type": "replied_to", "id": "100"}],
        },
        "180": {
            "id": "180", "cached_epoch": cache_epoch, "author_id": "200", "conversation_id": "700",
            "text": "Newest capped sibling.", "post_type": "author_cap_context",
            "referenced_tweets": [{"type": "replied_to", "id": "100"}],
        },
        "190": {
            "id": "190", "cached_epoch": cache_epoch, "author_id": "201", "conversation_id": "700",
            "text": "Other author.", "post_type": "author_cap_context",
            "referenced_tweets": [],
        },
        "200": {
            "id": "200", "cached_epoch": cache_epoch, "author_id": "200", "conversation_id": "701",
            "text": "Other conversation.", "post_type": "author_cap_context",
            "referenced_tweets": [],
        },
    }
    monkeypatch.setattr(
        bot,
        "get_tweet_by_id",
        lambda *_args, **_kwargs: pytest.fail("context must use tweet_cache"),
    )

    context, should_continue = bot.build_context_for_reply_ai(mention, state)

    assert should_continue is True
    assert [post["post_id"] for post in context["parent_thread"]] == ["150", "170", "180"]
    assert sum(post["post_id"] == "150" for post in context["parent_thread"]) == 1
    assert all(post["post_id"] not in {"190", "200"} for post in context["parent_thread"])


def test_author_cap_context_quote_commentary_recovers_original_from_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    cache_epoch = bot.now_epoch()
    mention = {
        "id": "920",
        "author_id": "200",
        "conversation_id": "910",
        "text": "Can you answer beneath my quote?",
        "referenced_tweets": [{"type": "replied_to", "id": "910"}],
    }
    state = bot.default_state()
    state["tweet_cache"] = {
        "900": {
            "id": "900", "cached_epoch": cache_epoch, "author_id": "12345", "conversation_id": "900",
            "text": "The original account post.", "referenced_tweets": [],
        },
        "910": {
            "id": "910", "cached_epoch": cache_epoch, "author_id": "200", "conversation_id": "910",
            "text": "My capped quote commentary.", "post_type": "author_cap_quote_context",
            "referenced_tweets": [{"type": "quoted", "id": "900"}],
        },
    }
    monkeypatch.setattr(
        bot,
        "get_tweet_by_id",
        lambda *_args, **_kwargs: pytest.fail("quote recovery must not fetch from X"),
    )

    context, should_continue = bot.build_context_for_reply_ai(mention, state)

    assert should_continue is True
    assert context["parent_thread"] == [
        {"post_id": "910", "author_role": "user", "text": "My capped quote commentary."},
    ]
    assert context["quoted_post"] == {
        "post_id": "900", "author_role": "account", "text": "The original account post.",
    }


def test_author_cap_context_quote_survives_parent_thread_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    cache_epoch = bot.now_epoch()
    mention = {
        "id": "960",
        "author_id": "200",
        "conversation_id": "910",
        "text": "A newer contribution in the same conversation.",
        "referenced_tweets": [],
    }
    state = bot.default_state()
    state["tweet_cache"] = {
        "900": {
            "id": "900", "cached_epoch": cache_epoch, "author_id": "12345",
            "conversation_id": "900", "text": "The original account post.",
            "referenced_tweets": [],
        },
        "910": {
            "id": "910", "cached_epoch": cache_epoch, "author_id": "200",
            "conversation_id": "910", "text": "Older capped quote commentary.",
            "post_type": "author_cap_quote_context",
            "referenced_tweets": [{"type": "quoted", "id": "900"}],
        },
        **{
            str(tweet_id): {
                "id": str(tweet_id), "cached_epoch": cache_epoch,
                "author_id": "200", "conversation_id": "910",
                "text": f"Newer capped context {tweet_id}.",
                "post_type": "author_cap_context", "referenced_tweets": [],
            }
            for tweet_id in (920, 930, 940, 950)
        },
    }
    monkeypatch.setattr(
        bot,
        "get_tweet_by_id",
        lambda *_args, **_kwargs: pytest.fail("cached cap context must not fetch from X"),
    )

    context, should_continue = bot.build_context_for_reply_ai(mention, state)

    assert should_continue is True
    assert [post["post_id"] for post in context["parent_thread"]] == ["930", "940", "950"]
    assert all(post["post_id"] != "910" for post in context["parent_thread"])
    assert context["quoted_post"] == {
        "post_id": "900", "author_role": "account", "text": "The original account post.",
    }


def test_trim_context_text_never_exceeds_requested_limit() -> None:
    for maximum in (0, 1, 2, 3, 20):
        assert len(bot.trim_context_text("ordinary words " * 20, maximum)) <= maximum


def test_quote_tweet_context_never_truncates_away_user_commentary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incoming = "INCOMING-QUOTE-MARKER courage and responsibility"
    monkeypatch.setattr(bot, "THREAD_CONTEXT_MAX_TOTAL_CHARS", 220)
    monkeypatch.setattr(bot, "THREAD_CONTEXT_MAX_CHARS_PER_POST", 500)

    context = bot.build_quote_tweet_reply_context(
        {"id": "900", "text": "original account post " + ("historical context " * 80)},
        {"id": "910", "conversation_id": "910", "author_id": "200", "text": incoming},
    )

    assert context["incoming_contribution"] == incoming
    assert context["quoted_post"]["post_id"] == "900"
    assert len(context["quoted_post"]["text"]) <= bot.THREAD_CONTEXT_MAX_CHARS_PER_POST


def test_meme_summary_context_limit_covers_current_analysis_shape() -> None:
    summary = (
        "2x2 grid meme: top-left Cuba 2016 rundown street, bottom-left Venezuela 2019 street scene with man on rubble, "
        "bottom cartoon 'Fantasy Land' candy castle; right column shows same man saying 'I PREFER REAL SOCIALISM', "
        "'I SAID REAL SOCIALISM', then 'PERFECTION' over the fantasy image Anti-socialist message: Real-world socialism "
        "produces poverty and failure; the only 'real socialism' that works is pure fantasy Analysis metadata: ranking 17, "
        "shareability high."
    )
    context = bot.tweet_context_text({"image_summary": summary})

    assert len(context) <= bot.THREAD_CONTEXT_MAX_CHARS_PER_POST
    assert bot.trim_context_text(context, bot.THREAD_CONTEXT_MAX_CHARS_PER_POST) == context


def test_schedule_next_quote_post_uses_configured_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    state: dict = {}
    monkeypatch.setattr(bot.random, "randint", lambda low, high: 123)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)

    bot.schedule_next_quote_post(state, from_epoch=1_000)

    assert state["next_quote_post_epoch"] == 1_123


def test_rate_limit_cooldown_uses_future_reset_with_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    state: dict = {}
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)

    bot.record_api_error(state, bot.ApiError("rate limited", service="x", status_code=429, reset_epoch=1_200), "x")

    assert state["api_cooldown_until_epoch"] == 1_260
    assert state["api_cooldown_reason"] == "x returned 429/rate limit"


def test_current_x_reply_not_permitted_403_is_terminal_not_transient() -> None:
    error = bot.ApiError(
        "X API error 403: You can only reply to or quote posts where you are mentioned or are the author.",
        service="x",
        status_code=403,
    )

    assert bot.api_error_is_reply_not_allowed(error) is True
    unrelated_auth_error = bot.ApiError(
        "X API error 403: {\"type\":\"https://api.x.com/2/problems/not-authorized-for-resource\","
        "\"detail\":\"This application is not permitted to perform that operation.\"}",
        service="x",
        status_code=403,
    )
    assert bot.api_error_is_reply_not_allowed(unrelated_auth_error) is False


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            bot.ApiError(
                "X API error 404: target not found",
                service="x",
                status_code=404,
                request_method="GET",
                request_path="/2/tweets/123",
            ),
            bot.X_API_ERROR_LOOKUP_TARGET_UNAVAILABLE,
        ),
        (
            bot.ApiError(
                "X API error 403: Tweet is unavailable",
                service="x",
                status_code=403,
                request_method="GET",
                request_path="/2/tweets/123",
            ),
            bot.X_API_ERROR_LOOKUP_TARGET_UNAVAILABLE,
        ),
        (
            bot.ApiError(
                "X API error 403: Invalid or expired token",
                service="x",
                status_code=403,
                request_method="GET",
                request_path="/2/tweets/123",
            ),
            bot.X_API_ERROR_GLOBAL_DENIAL,
        ),
        (
            bot.ApiError(
                "X API error 404: endpoint not found",
                service="x",
                status_code=404,
                request_method="GET",
                request_path="/2/users/123/mentions",
            ),
            bot.X_API_ERROR_ENDPOINT_NOT_FOUND,
        ),
        (
            bot.ApiError(
                "X API error 403: You attempted to reply to a Tweet "
                "that is deleted or not visible to you.",
                service="x",
                status_code=403,
                request_method="POST",
                request_path="/2/tweets",
            ),
            bot.X_API_ERROR_REPLY_TARGET_UNAVAILABLE,
        ),
    ],
)
def test_x_api_error_classifier_uses_endpoint_status_and_message(
    error: bot.ApiError,
    expected: str,
) -> None:
    assert bot.classify_x_api_error(error) == expected


def test_x_request_preserves_method_and_path_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(
        status_code=404,
        text='{"detail":"endpoint not found"}',
        headers={},
    )
    monkeypatch.setattr(bot.requests, "request", lambda *_args, **_kwargs: response)

    with pytest.raises(bot.ApiError) as caught:
        bot.x_request("GET", "/2/unsupported")

    assert caught.value.request_method == "GET"
    assert caught.value.request_path == "/2/unsupported"
    assert (
        bot.classify_x_api_error(caught.value)
        == bot.X_API_ERROR_ENDPOINT_NOT_FOUND
    )


def test_status_only_generic_403_and_404_are_not_target_terminal() -> None:
    auth_error = bot.ApiError(
        "X API error 403: operation forbidden",
        service="x",
        status_code=403,
    )
    missing_endpoint = bot.ApiError(
        "X API error 404: endpoint not found",
        service="x",
        status_code=404,
    )

    assert not bot.api_error_is_permanent_target_failure(auth_error)
    assert not bot.api_error_is_reply_not_allowed(auth_error)
    assert not bot.api_error_is_permanent_target_failure(missing_endpoint)
    assert not bot.api_error_is_reply_not_allowed(missing_endpoint)


def test_parent_and_quoted_lookup_only_suppress_target_specific_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mention = {
        "id": "200",
        "author_id": "300",
        "text": "A reply",
        "referenced_tweets": [{"type": "replied_to", "id": "123"}],
    }
    unavailable = bot.ApiError(
        "X API error 404: target not found",
        service="x",
        status_code=404,
        request_method="GET",
        request_path="/2/tweets/123",
    )
    monkeypatch.setattr(
        bot,
        "get_tweet_by_id_cached",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(unavailable),
    )

    assert bot.build_parent_chain(mention, {}) == []
    quoted = {
        **mention,
        "referenced_tweets": [{"type": "quoted", "id": "123"}],
    }
    assert bot._quoted_post_for_reply_context(quoted, {}) == {
        "post_id": "123",
        "author_role": "unknown",
        "text": "[Quoted post unavailable.]",
    }

    global_denial = bot.ApiError(
        "X API error 403: Invalid or expired token",
        service="x",
        status_code=403,
        request_method="GET",
        request_path="/2/tweets/123",
    )
    monkeypatch.setattr(
        bot,
        "get_tweet_by_id_cached",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(global_denial),
    )
    with pytest.raises(bot.ApiError, match="expired token"):
        bot.build_parent_chain(mention, {})
    with pytest.raises(bot.ApiError, match="expired token"):
        bot._quoted_post_for_reply_context(quoted, {})


def test_deleted_reply_target_403_is_terminal_not_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_000)
    deleted_target = bot.ApiError(
        "X API error 403: You attempted to reply to a Tweet "
        "that is deleted or not visible to you.",
        service="x",
        status_code=403,
        request_method="POST",
        request_path="/2/tweets",
    )

    assert bot.api_error_is_reply_not_allowed(deleted_target)
    bot.record_api_error(state, deleted_target, "x", scope="write")

    assert state["x_write_error_epochs"] == []
    assert state["x_write_api_cooldown_until_epoch"] == 0


def test_global_post_create_403_remains_in_transient_error_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_000)
    global_denial = bot.ApiError(
        "X API error 403: This application is not permitted to perform that operation.",
        service="x",
        status_code=403,
        request_method="POST",
        request_path="/2/tweets",
    )

    assert not bot.api_error_is_reply_not_allowed(global_denial)
    bot.record_api_error(state, global_denial, "x", scope="write")

    assert state["x_write_error_epochs"] == [1_000]


def test_reply_not_permitted_403_does_not_enter_write_error_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_000)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    error = bot.ApiError(
        "X API error 403: You can only reply to or quote posts where you are mentioned or are the author.",
        service="x",
        status_code=403,
    )

    bot.record_api_error(state, error, "x", scope="write")

    assert state["x_write_error_epochs"] == []
    assert state["x_write_api_cooldown_until_epoch"] == 0


def test_reply_target_eligibility_uses_only_target_author_or_direct_mention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MY_USERNAME", "MrsMThatcher")

    assert bot.reply_target_is_directly_eligible({"author_id": "12345", "text": "Own post"})
    assert bot.reply_target_is_directly_eligible({
        "author_id": "200",
        "text": "A direct reply",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
    })
    assert bot.reply_target_is_directly_eligible({
        "author_id": "200",
        "text": "@MrsMThatcher a cached direct mention",
    })
    assert not bot.reply_target_is_directly_eligible({
        "author_id": "200",
        "text": "The bot appears only in the parent",
        "referenced_tweets": [{"type": "replied_to", "id": "900"}],
    })
    assert not bot.reply_target_is_directly_eligible({
        "author_id": "200",
        "text": "Quoted text says @MrsMThatcher, but X did not mark it as a direct mention",
        "entities": {"mentions": [{"id": "999", "username": "SomeoneElse"}]},
    })


def test_hot_post_search_skips_ineligible_targets_before_candidate_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    replies = [
        {
            "id": "101",
            "author_id": "201",
            "text": "An organic sub-thread contribution.",
            "conversation_id": "900",
            "referenced_tweets": [{"type": "replied_to", "id": "900"}],
            "entities": {"mentions": []},
        },
        {
            "id": "102",
            "author_id": "202",
            "text": "@MrsMThatcher a directly eligible contribution.",
            "conversation_id": "900",
            "referenced_tweets": [{"type": "replied_to", "id": "900"}],
            "entities": {
                "mentions": [{"id": "12345", "username": "MrsMThatcher"}],
            },
        },
    ]

    monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", True)
    monkeypatch.setattr(bot, "MAX_HOT_POST_REPLIES_PER_CHECK", 1)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MY_USERNAME", "MrsMThatcher")
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "load_extra_quote_watch_post_ids", lambda: ["900"])
    monkeypatch.setattr(
        bot,
        "x_paginated_get",
        lambda *_args, **_kwargs: {"data": [dict(row) for row in replies], "_pagination": {}},
    )
    monkeypatch.setattr(bot, "attach_media_to_tweets", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    candidates = bot.get_hot_post_reply_candidates(state)

    assert [candidate["id"] for candidate in candidates] == ["102"]
    assert state["reply_evaluation_records"]["101"]["outcome"] == "reply_not_permitted"
    assert state["skipped_hot_reply_records"]["101"]["reason"] == (
        "target_does_not_directly_mention_account"
    )


def test_deterministic_spam_skip_precedes_context_media_retrieval_and_xai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0
    mention = {
        "id": "100",
        "author_id": "200",
        "text": "@MrsMThatcher guaranteed profit!!!!!",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "conversation_id": "100",
        "referenced_tweets": [],
    }

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MY_USERNAME", "MrsMThatcher")
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "reconcile_confirmed_reply_receipt", lambda _state: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: [dict(mention)])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "build_context_for_reply_ai", lambda *_args: pytest.fail("context must not be built"))
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: pytest.fail("media must not be prepared"))
    monkeypatch.setattr(bot, "generate_ai_first_reply", lambda *_args, **_kwargs: pytest.fail("retrieval/xAI must not be called"))
    monkeypatch.setattr(bot, "create_post", lambda *_args, **_kwargs: pytest.fail("X write must not be called"))
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert state["daily_reply_count"] == 0


def test_strategy_persistence_failure_blocks_mention_x_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0
    mention = {
        "id": "100",
        "author_id": "200",
        "text": "@MrsMThatcher Institutions endure when people defend their purpose.",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "conversation_id": "100",
        "referenced_tweets": [],
    }
    text = "Institutions endure only when people defend their purpose."
    context = unit_reply_context(target_id="100", contribution=mention["text"])

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MY_USERNAME", "MrsMThatcher")
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "reconcile_confirmed_reply_receipt", lambda _state: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: [dict(mention)])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    monkeypatch.setattr(bot, "build_context_for_reply_ai", lambda *_args: (context, True))
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        bot,
        "generate_ai_first_reply",
        lambda actual_context, *_args, **_kwargs: unit_approved_reply(
            actual_context,
            text=text,
            mode="opinion_or_principle",
        ),
    )
    monkeypatch.setattr(bot, "store_pending_ai_reply", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda *_args, **_kwargs: pytest.fail("X write must not be called"),
    )
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert state["daily_reply_count"] == 0
    assert state["reply_evaluation_records"]["100"]["reason"] == (
        "ai_reply_persistence_validation_failed"
    )


def test_ineligible_truncated_mention_is_terminal_before_context_media_or_xai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0
    mention = {
        "id": "2077713953983987776",
        "author_id": "352335305",
        "text": "Politics has no place in sport.",
        "entities": {"mentions": [{"id": "999", "username": "Argentina"}]},
        "conversation_id": "2077713953983987776",
        "referenced_tweets": [],
        "_pagination_truncated": True,
    }
    events: list[tuple[str, dict]] = []
    context = unit_reply_context(target_id=mention["id"], contribution=mention["text"])
    reply = unit_approved_reply(context, text="A persisted approved reply.")
    assert bot.store_pending_ai_reply(
        state, mention["id"], "mention", reply, context=context,
    )

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MY_USERNAME", "MrsMThatcher")
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: [dict(mention)])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "build_context_for_reply_ai", lambda *_args: pytest.fail("context must not be built"))
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: pytest.fail("media must not be prepared"))
    monkeypatch.setattr(bot, "generate_ai_first_reply", lambda *_args, **_kwargs: pytest.fail("xAI must not be called"))
    monkeypatch.setattr(bot, "create_post", lambda *_args, **_kwargs: pytest.fail("X write must not be called"))
    monkeypatch.setattr(bot, "log_event", lambda name, **values: events.append((name, values)))
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED

    record = state["reply_evaluation_records"][mention["id"]]
    assert record["outcome"] == "reply_not_permitted"
    assert record["reason"] == "target_does_not_directly_mention_account"
    assert sum(name == "reply_target_terminal" for name, _values in events) == 1
    assert not state.get("pending_ai_reply_drafts")
    strategy_events = [values for name, values in events if name == "ai_reply_pipeline_outcome"]
    assert len(strategy_events) == 1
    assert strategy_events[0]["status"] == "posting_failed_terminal"
    assert strategy_events[0]["failure_reason"] == "reply_not_permitted_preflight"


def test_posting_generic_reply_403_is_retry_blocking_not_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_reply_epoch"] = 0
    mention = {
        "id": "100",
        "author_id": "200",
        "text": "@MrsMThatcher a substantive direct mention",
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
        "conversation_id": "100",
        "referenced_tweets": [],
    }
    context = unit_reply_context(target_id="100", contribution=mention["text"])
    events: list[tuple[str, dict]] = []
    error = bot.ApiError(
        "X API error 403: You can only reply to or quote posts where you are mentioned or are the author.",
        service="x",
        status_code=403,
    )

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "DRY_RUN_REPLIES", False)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 5)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MY_USERNAME", "MrsMThatcher")
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "get_mentions", lambda _state: [dict(mention)])
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", lambda _state: [])
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", lambda _text: False)
    monkeypatch.setattr(bot, "build_context_for_reply_ai", lambda *_args: (context, True))
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        bot,
        "generate_ai_first_reply",
        lambda actual_context, *_args, **_kwargs: unit_approved_reply(actual_context),
    )
    monkeypatch.setattr(bot, "create_post", lambda **_kwargs: (_ for _ in ()).throw(error))
    monkeypatch.setattr(bot, "log_event", lambda name, **values: events.append((name, values)))
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.maybe_reply_to_mentions(state)

    status, receipt = bot.load_confirmed_reply_receipt()
    assert status == "sending"
    assert receipt is not None
    assert receipt["target_id"] == "100"
    strategy_events = [values for name, values in events if name == "ai_reply_pipeline_outcome"]
    assert len(strategy_events) == 1
    assert strategy_events[0]["status"] == "posting_failed_retryable"
    assert strategy_events[0]["strategy_version"] == STRATEGY_VERSION
    assert strategy_events[0]["reviewer_verdict"] == "approve"
    assert strategy_events[0]["failure_reason"] == "ambiguous_remote_outcome"


@pytest.mark.parametrize("reset_epoch", [None, 900])
def test_rate_limit_cooldown_falls_back_for_missing_or_past_reset(
    monkeypatch: pytest.MonkeyPatch,
    reset_epoch: int | None,
) -> None:
    state: dict = {}
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)

    bot.record_api_error(state, bot.ApiError("rate limited", service="x", status_code=429, reset_epoch=reset_epoch), "x")

    assert state["api_cooldown_until_epoch"] == 1_000 + bot.COOLDOWN_AFTER_429_SECONDS


def test_clear_expired_api_cooldowns_clears_only_expired_values(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {
        "api_cooldown_until_epoch": 900,
        "api_cooldown_reason": "old",
        "quote_api_cooldown_until_epoch": 1_100,
        "quote_api_cooldown_reason": "still active",
    }
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_000)

    assert bot.clear_expired_api_cooldowns(state) is True
    assert state["api_cooldown_until_epoch"] == 0
    assert state["api_cooldown_reason"] == ""
    assert state["quote_api_cooldown_until_epoch"] == 1_100
    assert state["quote_api_cooldown_reason"] == "still active"


def test_sanitize_next_reply_lane_priority_normalizes_invalid_value() -> None:
    state = {"next_reply_lane_priority": "sideways"}

    assert bot.sanitize_next_reply_lane_priority(state) is True
    assert state["next_reply_lane_priority"] == "normal"


def test_sanitize_next_reply_lane_priority_accepts_valid_value() -> None:
    state = {"next_reply_lane_priority": "quote"}

    assert bot.sanitize_next_reply_lane_priority(state) is False
    assert state["next_reply_lane_priority"] == "quote"


def test_local_config_coercion_accepts_boolean_strings_and_rejects_boolean_ints() -> None:
    assert bot._coerce_local_config_value("ENABLE_AUTO_REPLIES", "false", True) is False
    assert bot._coerce_local_config_value("ENABLE_AUTO_REPLIES", "yes", False) is True

    with pytest.raises(ValueError):
        bot._coerce_local_config_value("MAX_AUTO_REPLIES_PER_DAY", True, 24)


def test_local_config_coercion_rejects_negative_and_nonpositive_timings() -> None:
    with pytest.raises(ValueError):
        bot._coerce_local_config_value("POST_SLEEP_MIN", -1, bot.POST_SLEEP_MIN)

    with pytest.raises(ValueError):
        bot._coerce_local_config_value("MAX_AUTO_REPLIES_PER_DAY", 0, bot.MAX_AUTO_REPLIES_PER_DAY)


def apply_local_config_for_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    data: dict[str, object],
    *,
    initial: dict[str, object] | None = None,
    expect_error: bool = False,
) -> dict[str, object]:
    config_file = tmp_path / "mrsMThatcher.local.json"
    config_file.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", config_file)
    for key, value in (initial or {}).items():
        monkeypatch.setattr(bot, key, value)
    before = {
        key: getattr(bot, key)
        for key in bot.LOCAL_CONFIG_ALLOWED_KEYS
        if hasattr(bot, key)
    }
    if expect_error:
        with pytest.raises(bot.LocalConfigError):
            bot.apply_local_config()
    else:
        bot.apply_local_config()
    return before


def test_local_config_interacting_invalid_overrides_are_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {"POST_SLEEP_MIN": 10000, "POST_SLEEP_MAX": 5000},
        initial={"POST_SLEEP_MIN": 7200, "POST_SLEEP_MAX": 9000},
        expect_error=True,
    )

    assert bot.POST_SLEEP_MIN == before["POST_SLEEP_MIN"] == 7200
    assert bot.POST_SLEEP_MAX == before["POST_SLEEP_MAX"] == 9000


def test_local_config_valid_multi_key_override_applies_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {
            "POST_SLEEP_MIN": 8000,
            "POST_SLEEP_MAX": 8200,
            "ENABLE_DAILY_MEME_POSTS": False,
            "MAX_MENTIONS_PER_CHECK": 10,
        },
        initial={
            "POST_SLEEP_MIN": 7200,
            "POST_SLEEP_MAX": 9000,
            "ENABLE_DAILY_MEME_POSTS": True,
            "MAX_MENTIONS_PER_CHECK": 5,
        },
    )

    assert bot.POST_SLEEP_MIN == 8000
    assert bot.POST_SLEEP_MAX == 8200
    assert bot.ENABLE_DAILY_MEME_POSTS is False
    assert bot.MAX_MENTIONS_PER_CHECK == 10


def test_local_config_unknown_key_rejects_whole_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {
            "ENABLE_AUTO_REPLY": False,
            "POST_SLEEP_MIN": 8000,
            "POST_SLEEP_MAX": 8200,
        },
        initial={
            "ENABLE_AUTO_REPLIES": True,
            "POST_SLEEP_MIN": 7200,
            "POST_SLEEP_MAX": 9000,
        },
        expect_error=True,
    )

    assert bot.ENABLE_AUTO_REPLIES is before["ENABLE_AUTO_REPLIES"] is True
    assert bot.POST_SLEEP_MIN == before["POST_SLEEP_MIN"] == 7200
    assert bot.POST_SLEEP_MAX == before["POST_SLEEP_MAX"] == 9000


def test_local_config_coercion_failure_rejects_whole_transaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {"POST_SLEEP_MIN": 8000, "POST_SLEEP_MAX": 8200, "ENABLE_AUTO_REPLIES": "maybe"},
        initial={
            "POST_SLEEP_MIN": 7200,
            "POST_SLEEP_MAX": 9000,
            "ENABLE_AUTO_REPLIES": True,
        },
        expect_error=True,
    )

    assert bot.POST_SLEEP_MIN == before["POST_SLEEP_MIN"] == 7200
    assert bot.POST_SLEEP_MAX == before["POST_SLEEP_MAX"] == 9000
    assert bot.ENABLE_AUTO_REPLIES is before["ENABLE_AUTO_REPLIES"] is True


def test_local_config_mixed_valid_and_invalid_values_do_not_partially_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {"POST_SLEEP_MIN": 8000, "POST_SLEEP_MAX": 8200, "MAX_MENTIONS_PER_CHECK": 1},
        initial={
            "POST_SLEEP_MIN": 7200,
            "POST_SLEEP_MAX": 9000,
            "MAX_MENTIONS_PER_CHECK": 5,
        },
        expect_error=True,
    )

    assert bot.POST_SLEEP_MIN == before["POST_SLEEP_MIN"] == 7200
    assert bot.POST_SLEEP_MAX == before["POST_SLEEP_MAX"] == 9000
    assert bot.MAX_MENTIONS_PER_CHECK == before["MAX_MENTIONS_PER_CHECK"] == 5


def test_local_config_unsupported_key_cannot_override_arbitrary_globals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_func = bot.log_json_debug
    before = apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {"log_json_debug": None, "X_API_BASE_URL": "https://evil.invalid", "POST_SLEEP_MIN": 7300, "POST_SLEEP_MAX": 7400},
        initial={"POST_SLEEP_MIN": 7200, "POST_SLEEP_MAX": 9000},
        expect_error=True,
    )

    assert bot.log_json_debug is original_func
    assert bot.POST_SLEEP_MIN == before["POST_SLEEP_MIN"] == 7200
    assert bot.POST_SLEEP_MAX == before["POST_SLEEP_MAX"] == 9000
    assert not hasattr(bot, "X_API_BASE_URL")


def test_local_config_existing_production_style_overrides_still_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {
            "ENABLE_AUTO_REPLIES": True,
            "MIN_SECONDS_BETWEEN_REPLIES": 1800,
            "MAX_AUTO_REPLIES_PER_DAY": 24,
            "MAX_QUOTE_REPLIES_PER_DAY": 12,
            "POST_SLEEP_MIN": 7200,
            "POST_SLEEP_MAX": 9000,
        },
        initial={
            "ENABLE_AUTO_REPLIES": False,
            "MIN_SECONDS_BETWEEN_REPLIES": 1,
            "MAX_AUTO_REPLIES_PER_DAY": 2,
            "MAX_QUOTE_REPLIES_PER_DAY": 1,
            "POST_SLEEP_MIN": 100,
            "POST_SLEEP_MAX": 200,
        },
    )

    assert bot.ENABLE_AUTO_REPLIES is True
    assert bot.MIN_SECONDS_BETWEEN_REPLIES == 1800
    assert bot.MAX_AUTO_REPLIES_PER_DAY == 24
    assert bot.MAX_QUOTE_REPLIES_PER_DAY == 12
    assert bot.POST_SLEEP_MIN == 7200
    assert bot.POST_SLEEP_MAX == 9000


def test_default_minimum_reply_spacing_is_30_minutes() -> None:
    assert bot.MIN_SECONDS_BETWEEN_REPLIES == 1800


def test_local_config_can_enable_generated_image_pool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    generated_dir = tmp_path / "generated"
    generated_analysis = tmp_path / "generated_image_analysis.json"
    apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {
            "ENABLE_GENERATED_IMAGE_POOL": True,
            "GENERATED_IMAGE_DIR": str(generated_dir),
            "GENERATED_IMAGE_GLOB": "*.png",
            "GENERATED_IMAGE_ANALYSIS_FILE": str(generated_analysis),
            "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST": 6,
            "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": 3,
        },
        initial={
            "ENABLE_GENERATED_IMAGE_POOL": False,
            "GENERATED_IMAGE_DIR": "",
            "GENERATED_IMAGE_GLOB": "*.jpg",
            "GENERATED_IMAGE_ANALYSIS_FILE": "",
            "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST": 4,
            "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": 2,
        },
    )

    assert bot.ENABLE_GENERATED_IMAGE_POOL is True
    assert bot.GENERATED_IMAGE_DIR == str(generated_dir)
    assert bot.GENERATED_IMAGE_GLOB == "*.png"
    assert bot.GENERATED_IMAGE_ANALYSIS_FILE == str(generated_analysis)
    assert bot.GENERATED_IMAGE_ORIGIN_QUOTE_BOOST == 6
    assert bot.GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN == 3


@pytest.mark.parametrize("bad_value", [-1, True, False, 2.0, 2.5, "2", "x"])
def test_local_config_rejects_invalid_generated_image_spacing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_value: object,
) -> None:
    before = apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {"GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": bad_value},
        initial={"GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": 2},
        expect_error=True,
    )

    assert bot.GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN == before["GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN"] == 2

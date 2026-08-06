from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest

from remote_write_safety_protocol import (
    ACTIVATION_BASENAME as REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_BASENAME,
)
from tests.helpers.protocol_activation import create_test_protocol_activation

_IMPORT_TEMPORARY = tempfile.TemporaryDirectory(
    prefix="mrsMThatcher-production-consistency-import-"
)
_IMPORT_BASE = Path(_IMPORT_TEMPORARY.name)
_IMPORT_ENV = {
    "MRS_TEST_MODE": "1",
    "MRS_BASE_DIR": str(_IMPORT_BASE),
    "MRS_LOG_FILE": str(_IMPORT_BASE / "test.log"),
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
_ORIGINAL_ENV = {name: os.environ.get(name) for name in _IMPORT_ENV}
os.environ.update(_IMPORT_ENV)

import historical_context_formatter as context_formatter  # noqa: E402
import historical_context_outbox as outbox_module  # noqa: E402
import mrsMThatcher2 as bot  # noqa: E402

_REAL_CREATE_POST = bot.create_post

for _name, _value in _ORIGINAL_ENV.items():
    if _value is None:
        os.environ.pop(_name, None)
    else:
        os.environ[_name] = _value


def _forbid(operation: str):
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


def _proved_failure_history_item(
    *,
    source_receipt: dict,
    source_sha256: str,
    failure: str = "RemoteOperationsPaused: paused before transport",
    updated_at: str = "2026-08-01T12:00:01Z",
) -> dict:
    """Return one exact failed-history row for a source receipt."""

    return {
        "parent_post_id": str(source_receipt["parent_post_id"]),
        "quote_id": str(source_receipt["quote_id"]),
        "reply_text": str(source_receipt["reply_text"]),
        "status": "failed",
        "failure": failure,
        "attempt_count": int(source_receipt["attempt_number"]),
        "updated_at": updated_at,
        "remote_outcome": "proved_non_success",
        "source_receipt_sha256": source_sha256,
        "source_receipt_attempt_number": int(source_receipt["attempt_number"]),
    }


def _seed_confirmed_context_transport(
    *,
    parent_id: str,
    quote_id: str,
    reply_post_id: str,
    source_attempt_number: int = 1,
):
    """Create one exact confirmed journal over an attempting outbox source."""

    reply_epoch = 1_800_000_000
    reply_text = "Context — A confirmed transport awaits local completion."
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_id,
        main_post_confirmed_epoch=reply_epoch,
        quote_id=quote_id,
        quote_text="A quotation with a locally recoverable confirmed reply.",
    )
    store.claim_attempt(parent_id, started_epoch=reply_epoch)
    source_receipt = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": parent_id,
        "quote_id": quote_id,
        "reply_text": reply_text,
        "reply_epoch": reply_epoch,
        "started_at": "2026-08-01T12:00:00Z",
        "attempt_number": source_attempt_number,
    }
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        source_receipt,
    )
    _bind_context_attempt_to_source_receipt(
        store,
        parent_id=parent_id,
        outbox_attempt=1,
        source_receipt=source_receipt,
    )
    store.mark_remote_transaction_started(parent_id, attempt_number=1)
    payload = {
        "text": reply_text,
        "reply": {"in_reply_to_tweet_id": parent_id},
    }
    binding = bot.bind_lane_transport_source(
        receipt_path=bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        receipt=source_receipt,
        lane="historical_context_reply",
        payload=payload,
    )
    authority = bot.begin_transport_transaction(
        receipt_path=bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        source_binding=binding,
    )
    journal_path = bot.journal_path_for_receipt(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
    )
    authority = bot.arm_transport_transaction(
        journal_path,
        authority,
        mutation_authority=bot.transaction_mutation_authority(
            "focused historical-context journal arming"
        ),
    )
    bot.consume_transport_authority(
        journal_path,
        authority,
        method="POST",
        request_path="/2/tweets",
        payload=payload,
        expected_receipt_path=bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    )
    bot.confirm_transport_transaction(
        journal_path,
        authority,
        mutation_authority=bot.transaction_mutation_authority(
            "focused historical-context journal confirmation"
        ),
        post_id=reply_post_id,
        confirmation_epoch=reply_epoch + 1,
    )
    return store, source_receipt, journal_path


def _install_real_context_worker_success_fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    parent_id: str,
    quote_id: str,
    reply_text: str,
    reply_post_id: str,
):
    """Install a local-only formatter and exact confirmed transport path."""

    from historical_context_source_roles import POLICY_VERSION

    outbox = bot.historical_context_outbox_store()
    outbox.enqueue(
        parent_id,
        main_post_confirmed_epoch=1_800_000_000,
        quote_id=quote_id,
        quote_text="A complete worker-to-store callback fixture.",
    )
    packet = {"quote_id": quote_id, "quote_text": "Canonical quotation."}
    formatted = {
        "text": reply_text,
        "formatter_version": context_formatter.HISTORICAL_CONTEXT_FORMATTER_V5,
        "template_variant": "test_confirmed_callback_forwarding",
        "meaning_included": False,
        "meaning_decision_reason": "Focused transaction fixture.",
        "raw_character_count": len(reply_text),
        "character_count": len(reply_text),
        "weighted_character_count": len(reply_text),
        "maximum_length": 4_000,
        "verification_label": "Exact wording",
        "source_class": "primary",
        "historical_confidence": "high",
        "confidence_dimensions": {
            name: "high"
            for name in (
                "attribution",
                "wording",
                "source_event",
                "date",
                "historical_context",
                "interpretation",
            )
        },
        "source_role_audit_version": POLICY_VERSION,
        "rendering_mode": context_formatter.PUBLIC_RENDERING_MODE,
        "shortening_applied": False,
    }
    gate = SimpleNamespace(
        available=True,
        ledger_sha256="a" * 64,
        projection_sha256="b" * 64,
        disposition=lambda _quote_id: None,
        reviewed_disposition=lambda _quote_id: None,
    )
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT",
        ({quote_id: packet}, set()),
    )
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_SEMANTIC_GATE", gate)
    monkeypatch.setattr(
        context_formatter,
        "packet_for_posted_quote",
        lambda *_args, **_kwargs: packet,
    )
    monkeypatch.setattr(
        context_formatter,
        "format_context_reply_public",
        lambda *_args, **_kwargs: formatted,
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_010)

    def local_transport(
        method,
        path,
        *,
        json,
        _remote_write_authorization,
        **_kwargs,
    ):
        assert (method, path) == ("POST", "/2/tweets")
        bot.consume_transport_authority(
            Path(_remote_write_authorization.journal_path),
            _remote_write_authorization,
            method=method,
            request_path=path,
            payload=json,
            expected_receipt_path=bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        )
        return {"data": {"id": reply_post_id}}

    monkeypatch.setattr(bot, "x_request", local_transport)
    monkeypatch.setattr(bot, "create_post", _REAL_CREATE_POST)
    return outbox


@pytest.fixture(autouse=True)
def isolated_incident_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def _quote_analysis(text: str) -> dict:
    quote_id = bot.quote_text_hash(text)
    source = f"{text}\n"
    return {
        "analysis_kind": "quotes",
        "schema_version": 2,
        "source": {
            "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest()
        },
        "line_index": {"1": quote_id},
        "items": {
            quote_id: {
                "text": text,
                "quote_hash": quote_id,
                "line_numbers": [1],
                "analysis": {
                    "seasonality": {
                        "hard_exclude_outside_windows": False,
                        "preferred_windows": [],
                        "relevance": "none",
                    }
                },
            }
        },
    }


def _regular_receipt(
    *,
    post_id: str = "950001",
    text: str = "Good quote.",
    quote_post_epoch: int = 1_800_000_000,
) -> dict:
    return {
        "schema_version": 1,
        "post_id": post_id,
        "quote_hash": bot.quote_text_hash(text),
        "image_basename": "t01.jpg",
        "quote_post_epoch": quote_post_epoch,
        "next_quote_post_epoch": quote_post_epoch + 7_200,
        "text": text,
    }


def test_context_semantic_block_does_not_remove_ordinary_quote_eligibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quote_text = "An ordinarily eligible quotation."
    quote_id = bot.quote_text_hash(quote_text)
    bot.LINES_FILE.write_text(f"{quote_text}\n", encoding="utf-8")
    def gate_disposition(candidate_id):
        return "future_correction_needed" if candidate_id == quote_id else None

    gate = SimpleNamespace(
        available=True,
        ledger_sha256="incident-ledger",
        projection_sha256="incident-projection",
        disposition=gate_disposition,
        reviewed_disposition=gate_disposition,
    )
    packet = {"quote_id": quote_id, "quote_text": quote_text}
    events: list[tuple[str, dict]] = []

    monkeypatch.setattr(bot, "load_quote_analysis", lambda: _quote_analysis(quote_text))
    monkeypatch.setattr(
        bot,
        "completed_research_quote_hashes",
        lambda: {quote_id},
    )
    monkeypatch.setattr(bot, "current_datetime", lambda: bot.datetime(2026, 7, 23))
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT",
        ({quote_id: packet}, set()),
    )
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_SEMANTIC_GATE", gate)
    monkeypatch.setattr(
        context_formatter,
        "packet_for_posted_quote",
        lambda *_args, **_kwargs: packet,
    )
    monkeypatch.setattr(
        context_formatter,
        "format_context_reply_public",
        _forbid("formatting semantically blocked context"),
    )
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda event, **fields: events.append((event, fields)),
    )

    before = bot.quote_candidates_for_current_cycle(set())
    context_result = bot.maybe_post_historical_context_reply(
        quote_hash=quote_id,
        quote_text=quote_text,
        parent_post_id="950001",
    )
    after = bot.quote_candidates_for_current_cycle(set())

    assert [item["quote_hash"] for item in before] == [quote_id]
    assert [item["quote_hash"] for item in after] == [quote_id]
    assert context_result["status"] == "skipped_future_policy"
    assert context_result["reason"] == "open_semantic_review"
    assert events[-1][0] == "historical_context_reply"
    assert events[-1][1]["status"] == "skipped_future_policy"
    assert not bot.LINES_USED_FILE.exists()
    assert not bot.STATE_FILE.exists()
    assert not bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.exists()


def test_confirmed_main_receipt_replay_has_exact_decoupling_order_and_no_x_repost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = _regular_receipt()
    bot.atomic_write_json(
        bot.REGULAR_POST_RECEIPT_FILE,
        receipt,
        durable=True,
    )
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: receipt["quote_post_epoch"])

    order: list[str] = []
    x_post_calls: list[dict] = []
    original_apply = bot.apply_regular_post_receipt
    original_enqueue = bot.enqueue_historical_context_obligation
    original_remove = bot.remove_regular_post_receipt

    def tracked_apply(*args, **kwargs):
        result = original_apply(*args, **kwargs)
        order.append("main_state_applied")
        return result

    def tracked_save(*_args, **_kwargs):
        order.append("main_state_saved")

    def tracked_enqueue(value: dict):
        obligation = original_enqueue(value)
        order.append("context_obligation_enqueued")
        return obligation

    def tracked_remove(receipt_to_remove: dict):
        assert receipt_to_remove == receipt
        assert bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.exists()
        original_remove(receipt_to_remove)
        order.append("main_receipt_removed")

    def context_only_attempt(**_kwargs):
        assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
        order.append("context_attempted")
        return {"status": "skipped_future_policy"}

    def forbidden_x_post(**kwargs):
        x_post_calls.append(kwargs)
        pytest.fail("confirmed receipt replay must never recreate the X main post")

    monkeypatch.setattr(bot, "apply_regular_post_receipt", tracked_apply)
    monkeypatch.setattr(bot, "save_regular_post_protected_state", tracked_save)
    monkeypatch.setattr(bot, "enqueue_historical_context_obligation", tracked_enqueue)
    monkeypatch.setattr(bot, "remove_regular_post_receipt", tracked_remove)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        context_only_attempt,
    )
    monkeypatch.setattr(bot, "create_post", forbidden_x_post)

    lines_used: set[str] = set()
    images_used: set[str] = set()
    state: dict = {}
    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is True
    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is False

    assert order == [
        "main_state_applied",
        "main_state_saved",
        "context_obligation_enqueued",
        "main_receipt_removed",
        "context_attempted",
    ]
    assert x_post_calls == []
    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
    assert receipt["quote_hash"] in lines_used
    assert receipt["image_basename"] in images_used
    assert state["last_main_post_id"] == receipt["post_id"]
    obligation = bot.historical_context_outbox_store().get(receipt["post_id"])
    assert obligation["main_post"]["state"] == "main_post_confirmed"
    assert (
        obligation["context_reply"]["state"]
        == "context_reply_not_required"
    )
    assert not bot.LINES_USED_FILE.exists()
    assert not bot.IMAGES_USED_FILE.exists()
    assert not bot.STATE_FILE.exists()
    assert not bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.exists()
    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()


def test_confirmed_main_reconciliation_survives_unexpected_auxiliary_worker_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = _regular_receipt(post_id="950002")
    bot.atomic_write_json(
        bot.REGULAR_POST_RECEIPT_FILE,
        receipt,
        durable=True,
    )
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: receipt["quote_post_epoch"])
    monkeypatch.setattr(
        bot,
        "save_regular_post_protected_state",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        bot,
        "process_due_historical_context_obligations",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("unexpected auxiliary worker fault")
        ),
    )

    lines_used: set[str] = set()
    images_used: set[str] = set()
    state: dict = {}
    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is True

    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
    assert state["last_main_post_id"] == receipt["post_id"]
    assert receipt["quote_hash"] in lines_used
    assert receipt["image_basename"] in images_used
    obligation = bot.historical_context_outbox_store().get(receipt["post_id"])
    assert obligation["main_post"]["state"] == "main_post_confirmed"
    assert obligation["context_reply"]["state"] == "context_reply_pending"
    assert bot._HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON


def test_quote_identity_normalisation_is_stable_without_punctuation_fuzzing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical_text = "Stable quotation identity — with punctuation."
    canonical_id = bot.quote_text_hash(canonical_text)
    assert bot.quote_text_hash(
        "  Stable quotation identity —\nwith punctuation.  "
    ) == canonical_id
    assert bot.quote_text_hash(
        "Stable quotation identity - with punctuation."
    ) != canonical_id

    packet = {"quote_id": canonical_id, "quote_text": canonical_text}
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT",
        ({canonical_id: packet}, set()),
    )
    monkeypatch.setattr(
        context_formatter,
        "packet_for_posted_quote",
        lambda _packets, _unresolved, _quote_hash, quote_text: (
            packet
            if " ".join(quote_text.split())
            == " ".join(canonical_text.split())
            else None
        ),
    )

    assert bot.canonical_context_obligation_quote_id(
        "9" * 64,
        " Stable quotation identity —\nwith punctuation. ",
    ) == canonical_id
    assert bot.canonical_context_obligation_quote_id(
        "9" * 64,
        "Stable quotation identity - with punctuation.",
    ) == "9" * 64


@pytest.mark.parametrize(
    "existing_context_state",
    ["pending", "retryable", "confirmed", "not_required"],
)
def test_main_receipt_replay_accepts_all_existing_outbox_terminal_and_active_states(
    existing_context_state: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = _regular_receipt(post_id="950002")
    bot.atomic_write_json(
        bot.REGULAR_POST_RECEIPT_FILE,
        receipt,
        durable=True,
    )
    store = bot.historical_context_outbox_store()
    if existing_context_state == "not_required":
        store.enqueue(
            receipt["post_id"],
            main_post_confirmed_epoch=receipt["quote_post_epoch"],
            not_required_reason="historical_context_reply_disabled",
        )
    else:
        store.enqueue(
            receipt["post_id"],
            main_post_confirmed_epoch=receipt["quote_post_epoch"],
            quote_id=receipt["quote_hash"],
            quote_text=receipt["text"],
        )
        if existing_context_state == "retryable":
            store.claim_attempt(
                receipt["post_id"],
                started_epoch=receipt["quote_post_epoch"],
            )
            store.record_retryable_failure(
                receipt["post_id"],
                attempt_number=1,
                error="temporary failure",
                failed_epoch=receipt["quote_post_epoch"] + 10,
            )
        elif existing_context_state == "confirmed":
            store.claim_attempt(
                receipt["post_id"],
                started_epoch=receipt["quote_post_epoch"],
            )
            store.record_confirmed(
                receipt["post_id"],
                attempt_number=1,
                reply_post_id="950003",
                confirmed_epoch=receipt["quote_post_epoch"] + 10,
            )

    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(
        bot,
        "now_epoch",
        lambda: receipt["quote_post_epoch"] + 20,
    )
    monkeypatch.setattr(
        bot,
        "save_regular_post_protected_state",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **_kwargs: {"status": "skipped_future_policy"},
    )

    assert bot.reconcile_regular_post_receipt(set(), set(), {}) is True
    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
    assert store.get(receipt["post_id"])["main_post"]["state"] == (
        "main_post_confirmed"
    )


def test_retryable_and_terminal_context_outcomes_preserve_confirmed_main_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quote_state = {
        "last_main_post_id": "810002",
        "last_quote_post_epoch": 8_000,
        "next_quote_post_epoch": 18_000,
    }
    state_before = copy.deepcopy(quote_state)
    sentinels = {
        bot.LINES_USED_FILE: b'["quote-sentinel"]\n',
        bot.IMAGES_USED_FILE: b'["image-sentinel"]\n',
        bot.STATE_FILE: b'{"last_main_post_id":"810002"}\n',
    }
    for path, payload in sentinels.items():
        path.write_bytes(payload)

    store = bot.historical_context_outbox_store()
    store.enqueue(
        "810001",
        main_post_confirmed_epoch=8_000,
        quote_id="a" * 64,
        quote_text="Retryable context quotation.",
    )
    store.enqueue(
        "810002",
        main_post_confirmed_epoch=8_001,
        quote_id="b" * 64,
        quote_text="Terminal context quotation.",
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 9_000)

    def context_result(*, parent_post_id: str, **_kwargs):
        if parent_post_id == "810001":
            return {"status": "failed", "error": "temporary formatter outage"}
        return {"status": "failed_terminal", "reason": "unsupported packet"}

    monkeypatch.setattr(bot, "maybe_post_historical_context_reply", context_result)

    results = bot.process_due_historical_context_obligations(limit=2)

    assert [item["parent_post_id"] for item in results] == ["810001", "810002"]
    assert store.get("810001")["main_post"]["state"] == "main_post_confirmed"
    assert (
        store.get("810001")["context_reply"]["state"]
        == "context_reply_failed_retryable"
    )
    assert store.get("810002")["main_post"]["state"] == "main_post_confirmed"
    assert (
        store.get("810002")["context_reply"]["state"]
        == "context_reply_failed_terminal"
    )
    assert quote_state == state_before
    for path, payload in sentinels.items():
        assert path.read_bytes() == payload
    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
    assert not bot.MEME_POST_RECEIPT_FILE.exists()


def test_requested_parent_is_not_starved_by_an_older_due_obligation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800001",
        main_post_confirmed_epoch=8_000,
        quote_id="d" * 64,
        quote_text="An older pending context.",
    )
    store.enqueue(
        "800002",
        main_post_confirmed_epoch=8_001,
        quote_id="e" * 64,
        quote_text="The requested pending context.",
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 9_000)
    attempted: list[str] = []

    def context_result(*, parent_post_id: str, **_kwargs):
        attempted.append(parent_post_id)
        return {"status": "skipped_future_policy"}

    monkeypatch.setattr(bot, "maybe_post_historical_context_reply", context_result)

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800002",
    )

    assert attempted == ["800002"]
    assert result[0]["context_reply_state"] == "context_reply_not_required"
    assert store.get("800001")["context_reply"]["state"] == "context_reply_pending"


def test_policy_disable_after_retry_becomes_terminal_without_retry_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800003",
        main_post_confirmed_epoch=8_000,
        quote_id="f" * 64,
        quote_text="A context obligation with one definite failure.",
    )
    store.claim_attempt("800003", started_epoch=8_000)
    store.record_retryable_failure(
        "800003",
        attempt_number=1,
        error="temporary preparation failure",
        failed_epoch=8_010,
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 9_000)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **_kwargs: {"status": "disabled"},
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800003",
    )

    assert result[0]["context_reply_state"] == "context_reply_failed_terminal"
    terminal = store.get("800003")["context_reply"]
    assert terminal["attempt_count"] == 2
    assert "disabled after a prior retryable" in terminal["failure"]["error"]
    assert bot.process_due_historical_context_obligations(
        parent_post_id="800003",
    ) == []


def test_process_scoped_context_incompatibility_defers_without_abandoning_outbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800004",
        main_post_confirmed_epoch=8_000,
        quote_id="1" * 64,
        quote_text="A context obligation retained for a corrected restart.",
    )
    before = store.snapshot()
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON",
        "RuntimeError: source-role policy mismatch",
    )
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("context attempt while process-scoped runtime is unavailable"),
    )

    assert bot.process_due_historical_context_obligations(limit=1) == []
    assert store.snapshot() == before


def test_outbox_outcome_write_failure_latches_retry_after_durable_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800005",
        main_post_confirmed_epoch=8_000,
        quote_id="4" * 64,
        quote_text="A durably claimed context attempt.",
    )
    writes = {"count": 0}
    real_write = outbox_module._atomic_write_json

    def fail_outcome_write(path: Path, value: dict) -> None:
        writes["count"] += 1
        if writes["count"] == 2:
            raise OSError("injected outbox outcome write failure")
        real_write(path, value)

    attempts: list[str] = []
    monkeypatch.setattr(outbox_module, "_atomic_write_json", fail_outcome_write)
    monkeypatch.setattr(bot, "now_epoch", lambda: 9_000)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **_kwargs: attempts.append("attempt") or {
            "status": "failed",
            "error": "definite preparation failure",
        },
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800005",
    )

    assert result[0]["status"] == "outbox_persistence_failed"
    claimed = store.get("800005")["context_reply"]
    assert claimed["state"] == "context_reply_attempting"
    assert claimed["attempt_count"] == 1
    assert attempts == ["attempt"]
    assert bot._HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON
    assert bot.process_due_historical_context_obligations(
        parent_post_id="800005",
    ) == []
    assert attempts == ["attempt"]


def test_definite_context_local_persistence_failure_never_creates_ambiguity_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A proved remote non-success stays a local recovery problem."""

    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800031",
        main_post_confirmed_epoch=8_000,
        quote_id="6" * 64,
        quote_text="A definite non-success with incomplete local persistence.",
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 9_000)
    def fail_after_exact_source_binding(**kwargs):
        kwargs["on_source_receipt_published"]("a" * 64, 1)
        raise context_formatter.DefiniteContextReplyLocalPersistenceError(
            "outbox callback did not finish",
            parent_post_id="800031",
            reply_text="Context",
            source_receipt_sha256="a" * 64,
            source_receipt_attempt_number=1,
            remote_error=bot.RemoteOperationsPaused("paused before transport"),
        )

    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        fail_after_exact_source_binding,
    )
    monkeypatch.setattr(
        bot,
        "record_ambiguous_remote_post",
        _forbid("a definite local persistence failure ambiguity marker"),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800031",
    )

    assert result == [
        {
            "parent_post_id": "800031",
            "status": "failed",
            "context_reply_state": "context_reply_failed_retryable",
        }
    ]
    assert (
        store.get("800031")["context_reply"]["state"]
        == "context_reply_failed_retryable"
    )
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()


def test_proved_context_failure_recovers_missing_history_from_exact_outbox_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A history fsync failure remains locally recoverable after restart."""

    parent_id = "800032"
    quote_id = "7" * 64
    outbox = bot.historical_context_outbox_store()
    outbox.enqueue(
        parent_id,
        main_post_confirmed_epoch=1_800_000_000,
        quote_id=quote_id,
        quote_text="A proved local non-success whose history write failed.",
    )
    outbox.claim_attempt(parent_id, started_epoch=1_800_000_001)
    reply_store = context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        mutation_authority_provider=bot.transaction_mutation_authority,
        retirement_uncertainty_callback=(
            bot.latch_source_receipt_retirement_uncertainty
        ),
    )
    monkeypatch.setattr(
        reply_store,
        "record_failure",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("injected history directory fsync failure")
        ),
    )

    def definite_local_pause(**kwargs):
        kwargs["on_remote_transaction_started"]()
        raise bot.RemoteOperationsPaused("paused before transport")

    with pytest.raises(
        context_formatter.DefiniteContextReplyLocalPersistenceError,
        match="failure history",
    ) as captured:
        reply_store.post(
            parent_post_id=parent_id,
            quote_id=quote_id,
            reply_text="Context — The request was definitely not transmitted.",
            create_post=definite_local_pause,
            now_epoch=lambda: 1_800_000_002,
            on_source_receipt_published=(
                lambda source_sha256, source_attempt: (
                    outbox.bind_attempt_source_receipt(
                        parent_id,
                        attempt_number=1,
                        source_receipt_sha256=source_sha256,
                        source_receipt_attempt_number=source_attempt,
                    )
                )
            ),
            on_remote_transaction_started=(
                lambda: outbox.mark_remote_transaction_started(
                    parent_id,
                    attempt_number=1,
                )
            ),
            on_definite_non_success=(
                lambda error: bot._record_context_outbox_failure(
                    outbox,
                    parent_post_id=parent_id,
                    attempt_number=1,
                    error=error,
                    failed_epoch=1_800_000_003,
                    proved_remote_non_success=True,
                )
            ),
            remote_failure_is_definite_non_success=lambda error: isinstance(
                error,
                bot.RemoteOperationsPaused,
            ),
        )

    assert captured.value.remote_error is not None
    failed_context = outbox.get(parent_id)["context_reply"]
    assert failed_context["state"] == "context_reply_failed_retryable"
    assert failed_context["failure"]["remote_outcome"] == "proved_non_success"
    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert not bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.exists()

    assert reply_store.ensure_proved_failure_history_from_outbox(failed_context)
    assert reply_store.reconcile_receipt_disposition() == "definite_failure"
    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    recovered = reply_store.history()["items"][parent_id]
    assert recovered["remote_outcome"] == "proved_non_success"
    assert recovered["source_receipt_sha256"] == failed_context["failure"][
        "source_receipt_sha256"
    ]


def test_new_proved_context_failure_advances_prior_source_attempt_history() -> None:
    """Exact attempt twenty-one may supersede exact failed attempt twenty."""

    parent_id = "800043"
    quote_id = "4" * 64
    prior_receipt = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": parent_id,
        "quote_id": quote_id,
        "reply_text": "Context — Prior proved source attempt.",
        "reply_epoch": 1_800_000_000,
        "started_at": "2026-08-01T12:00:00Z",
        "attempt_number": 20,
    }
    prior_sha256 = hashlib.sha256(
        context_formatter.canonical_json_bytes(prior_receipt)
    ).hexdigest()
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {
            "schema_version": 1,
            "items": {
                parent_id: _proved_failure_history_item(
                    source_receipt=prior_receipt,
                    source_sha256=prior_sha256,
                )
            },
        },
    )
    current_receipt = {
        **prior_receipt,
        "reply_text": "Context — Current proved source attempt.",
        "reply_epoch": 1_800_000_010,
        "started_at": "2026-08-01T12:00:10Z",
        "attempt_number": 21,
    }
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        current_receipt,
    )
    current_sha256 = hashlib.sha256(
        context_formatter.canonical_json_bytes(current_receipt)
    ).hexdigest()
    failed_context = {
        "state": "context_reply_failed_retryable",
        "quote_id": quote_id,
        "quote_text": "Canonical quotation.",
        "attempt_count": 1,
        "failure": {
            "attempt_number": 1,
            "failed_epoch": 1_800_000_011,
            "error": "RemoteOperationsPaused: paused before transport",
            "remote_outcome": "proved_non_success",
            "source_receipt_sha256": current_sha256,
            "source_receipt_attempt_number": 21,
        },
        "next_attempt_epoch": 1_800_000_071,
        "backoff_seconds": 60,
        "updated_epoch": 1_800_000_011,
    }
    reply_store = context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        mutation_authority_provider=bot.transaction_mutation_authority,
    )

    assert reply_store.ensure_proved_failure_history_from_outbox(failed_context)
    current = reply_store.history()["items"][parent_id]
    assert current["attempt_count"] == 21
    assert current["reply_text"] == current_receipt["reply_text"]
    assert current["source_receipt_sha256"] == current_sha256


def test_real_context_worker_wires_exact_source_before_remote_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protect every worker-to-store callback edge with one real entrypoint."""

    from historical_context_source_roles import POLICY_VERSION

    parent_id = "800033"
    quote_id = "8" * 64
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_id,
        main_post_confirmed_epoch=1_800_000_000,
        quote_id=quote_id,
        quote_text="A quotation requiring one historical context reply.",
    )
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {
            "schema_version": 1,
            "items": {
                parent_id: {
                    "parent_post_id": parent_id,
                    "quote_id": quote_id,
                    "reply_text": "Earlier local failure.",
                    "status": "failed",
                    "failure": "RuntimeError: legacy failure",
                    "attempt_count": 20,
                    "updated_at": "2026-07-31T23:59:59Z",
                }
            },
        },
    )
    packet = {"quote_id": quote_id, "quote_text": "Canonical quotation."}
    formatted_text = "Context — A definite local pause prevented transmission."
    formatted = {
        "text": formatted_text,
        "formatter_version": context_formatter.HISTORICAL_CONTEXT_FORMATTER_V5,
        "template_variant": "test_exact_source_binding",
        "meaning_included": False,
        "meaning_decision_reason": "Focused transactional fixture.",
        "raw_character_count": len(formatted_text),
        "character_count": len(formatted_text),
        "weighted_character_count": len(formatted_text),
        "maximum_length": 4_000,
        "verification_label": "Exact wording",
        "source_class": "primary",
        "historical_confidence": "high",
        "confidence_dimensions": {
            name: "high"
            for name in (
                "attribution",
                "wording",
                "source_event",
                "date",
                "historical_context",
                "interpretation",
            )
        },
        "source_role_audit_version": POLICY_VERSION,
        "rendering_mode": context_formatter.PUBLIC_RENDERING_MODE,
        "shortening_applied": False,
    }
    gate = SimpleNamespace(
        available=True,
        ledger_sha256="a" * 64,
        projection_sha256="b" * 64,
        disposition=lambda _quote_id: None,
        reviewed_disposition=lambda _quote_id: None,
    )
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT",
        ({quote_id: packet}, set()),
    )
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_SEMANTIC_GATE", gate)
    monkeypatch.setattr(
        context_formatter,
        "packet_for_posted_quote",
        lambda *_args, **_kwargs: packet,
    )
    monkeypatch.setattr(
        context_formatter,
        "format_context_reply_public",
        lambda *_args, **_kwargs: formatted,
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_010)
    order: list[str] = []

    real_publish = context_formatter.publish_exact_source_receipt_document
    real_bind = outbox_module.HistoricalContextOutbox.bind_attempt_source_receipt
    real_mark = outbox_module.HistoricalContextOutbox.mark_remote_transaction_started
    real_outcome = bot._record_context_outbox_failure
    real_history = context_formatter.HistoricalContextReplyStore.record_failure
    real_retire = context_formatter.HistoricalContextReplyStore._retire_exact_receipt
    real_arm = bot.arm_transport_transaction
    real_abort = bot.abort_untransmitted_transport_transaction

    def publish(*args, **kwargs):
        result = real_publish(*args, **kwargs)
        order.append("receipt_published")
        return result

    def bind(self, *args, **kwargs):
        result = real_bind(self, *args, **kwargs)
        order.append("source_bound")
        return result

    def mark(self, *args, **kwargs):
        result = real_mark(self, *args, **kwargs)
        order.append("remote_started")
        return result

    def outcome(*args, **kwargs):
        result = real_outcome(*args, **kwargs)
        order.append("outbox_failure")
        return result

    def history(self, *args, **kwargs):
        result = real_history(self, *args, **kwargs)
        order.append("history_failure")
        return result

    def retire(self, *args, **kwargs):
        result = real_retire(self, *args, **kwargs)
        order.append("receipt_retired")
        return result

    def arm(*args, **kwargs):
        receipt = context_formatter.HistoricalContextReplyStore(
            bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
            bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        )._load_receipt_safely()[0]
        receipt_bytes = context_formatter.canonical_json_bytes(receipt)
        current = store.get(parent_id)["context_reply"]
        assert current["source_receipt_sha256"] == hashlib.sha256(
            receipt_bytes
        ).hexdigest()
        assert current["source_receipt_attempt_number"] == receipt["attempt_number"]
        assert receipt["attempt_number"] == 21
        order.append("journal_armed")
        return real_arm(*args, **kwargs)

    def pause_at_transport_boundary(
        method,
        path,
        *,
        _remote_write_authorization=None,
        **_kwargs,
    ):
        assert (method, path) == ("POST", "/2/tweets")
        assert _remote_write_authorization is not None
        assert store.get(parent_id)["context_reply"][
            "remote_transaction_started"
        ] is True
        order.append("transport_boundary")
        raise bot.RemoteOperationsPaused("focused local pause")

    def abort(*args, **kwargs):
        result = real_abort(*args, **kwargs)
        order.append("journal_aborted")
        return result

    monkeypatch.setattr(
        context_formatter,
        "publish_exact_source_receipt_document",
        publish,
    )
    monkeypatch.setattr(
        outbox_module.HistoricalContextOutbox,
        "bind_attempt_source_receipt",
        bind,
    )
    monkeypatch.setattr(
        outbox_module.HistoricalContextOutbox,
        "mark_remote_transaction_started",
        mark,
    )
    monkeypatch.setattr(bot, "_record_context_outbox_failure", outcome)
    monkeypatch.setattr(
        context_formatter.HistoricalContextReplyStore,
        "record_failure",
        history,
    )
    monkeypatch.setattr(
        context_formatter.HistoricalContextReplyStore,
        "_retire_exact_receipt",
        retire,
    )
    monkeypatch.setattr(bot, "arm_transport_transaction", arm)
    monkeypatch.setattr(bot, "x_request", pause_at_transport_boundary)
    monkeypatch.setattr(bot, "abort_untransmitted_transport_transaction", abort)
    monkeypatch.setattr(bot, "create_post", _REAL_CREATE_POST)

    result = bot.process_due_historical_context_obligations(parent_post_id=parent_id)

    assert result == [
        {
            "parent_post_id": parent_id,
            "status": "failed",
            "context_reply_state": "context_reply_failed_retryable",
        }
    ]
    assert order == [
        "receipt_published",
        "source_bound",
        "journal_armed",
        "remote_started",
        "transport_boundary",
        "journal_aborted",
        "outbox_failure",
        "history_failure",
        "receipt_retired",
    ]
    final_context = store.get(parent_id)["context_reply"]
    assert final_context["failure"]["remote_outcome"] == "proved_non_success"
    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()


def test_confirmed_context_journal_requires_consistent_attempting_outbox() -> None:
    """A confirmed journal cannot override contradictory durable outbox proof."""

    parent_id = "800035"
    store, source_receipt, journal_path = _seed_confirmed_context_transport(
        parent_id=parent_id,
        quote_id="c" * 64,
        reply_post_id="980035",
    )
    store.record_retryable_failure(
        parent_id,
        attempt_number=1,
        error="contradictory synthetic local non-success",
        failed_epoch=1_800_000_002,
        proved_remote_non_success=True,
    )
    receipt_before = bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes()
    journal_before = journal_path.read_bytes()
    outbox_before = bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.read_bytes()

    bot.reconcile_runtime_historical_context_state()

    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes() == receipt_before
    assert journal_path.read_bytes() == journal_before
    assert bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.read_bytes() == outbox_before

    with pytest.raises(
        RuntimeError,
        match="confirmed historical-context transport conflicts",
    ):
        bot.reconcile_confirmed_transactions_before_global_barrier(
            set(),
            set(),
            {},
        )

    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes() == receipt_before
    assert journal_path.read_bytes() == journal_before
    assert bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.read_bytes() == outbox_before
    assert source_receipt["lifecycle_state"] == "sending"
    assert not bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.exists()


def test_confirmed_context_journal_completes_outbox_history_and_receipt() -> None:
    """One exact confirmed pair completes every local context authority."""

    parent_id = "800036"
    store, source_receipt, journal_path = _seed_confirmed_context_transport(
        parent_id=parent_id,
        quote_id="d" * 64,
        reply_post_id="980036",
    )

    result = bot.reconcile_confirmed_transactions_before_global_barrier(
        set(),
        set(),
        {},
    )

    assert result["historical_context"] is True
    context_reply = store.get(parent_id)["context_reply"]
    assert context_reply["state"] == "context_reply_confirmed"
    assert context_reply["reply_post_id"] == "980036"
    history_item = context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    ).history()["items"][parent_id]
    assert history_item["status"] == "completed"
    assert history_item["reply_post_id"] == "980036"
    assert history_item["source_receipt_sha256"] == hashlib.sha256(
        context_formatter.canonical_json_bytes(source_receipt)
    ).hexdigest()
    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert not journal_path.exists()


def test_confirmed_context_accepts_independent_source_attempt_ordinal() -> None:
    """Outbox attempt one may confirm exact source-history attempt twenty-one."""

    parent_id = "800040"
    store, source_receipt, journal_path = _seed_confirmed_context_transport(
        parent_id=parent_id,
        quote_id="1" * 64,
        reply_post_id="980040",
        source_attempt_number=21,
    )
    store.record_confirmed(
        parent_id,
        attempt_number=1,
        reply_post_id="980040",
        confirmed_epoch=1_800_000_001,
    )

    result = bot.reconcile_confirmed_transactions_before_global_barrier(
        set(),
        set(),
        {},
    )

    assert result["historical_context"] is True
    assert store.get(parent_id)["context_reply"]["attempt_count"] == 1
    history_item = context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    ).history()["items"][parent_id]
    assert history_item["attempt_number"] == 21
    assert history_item["reply_post_id"] == "980040"
    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert not journal_path.exists()


def test_promoted_context_receipt_cannot_consume_contradictory_outbox() -> None:
    """The post-promotion crash state still requires one consistent outbox."""

    parent_id = "800037"
    store, source_receipt, journal_path = _seed_confirmed_context_transport(
        parent_id=parent_id,
        quote_id="e" * 64,
        reply_post_id="980037",
    )
    context_store = context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        mutation_authority_provider=bot.transaction_mutation_authority,
    )
    context_store.promote_sending_receipt_from_confirmed_transport(
        source_receipt,
        reply_post_id="980037",
        confirmation_epoch=1_800_000_001,
        require_confirmed_transport=True,
    )
    store.record_retryable_failure(
        parent_id,
        attempt_number=1,
        error="post-promotion local persistence failure",
        failed_epoch=1_800_000_002,
        proved_remote_non_success=True,
    )
    receipt_before = bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes()
    journal_before = journal_path.read_bytes()
    outbox_before = bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.read_bytes()

    bot.reconcile_runtime_historical_context_state()

    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes() == receipt_before
    assert journal_path.read_bytes() == journal_before
    assert bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.read_bytes() == outbox_before
    with pytest.raises(
        RuntimeError,
        match="confirmed historical-context transport conflicts",
    ):
        bot.reconcile_confirmed_transactions_before_global_barrier(
            set(),
            set(),
            {},
        )

    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes() == receipt_before
    assert journal_path.read_bytes() == journal_before
    assert bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.read_bytes() == outbox_before
    assert not bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.exists()


def test_promoted_context_receipt_confirms_outbox_before_local_completion() -> None:
    """The post-promotion crash state closes its outbox before retirement."""

    parent_id = "800038"
    store, source_receipt, journal_path = _seed_confirmed_context_transport(
        parent_id=parent_id,
        quote_id="f" * 64,
        reply_post_id="980038",
    )
    context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        mutation_authority_provider=bot.transaction_mutation_authority,
    ).promote_sending_receipt_from_confirmed_transport(
        source_receipt,
        reply_post_id="980038",
        confirmation_epoch=1_800_000_001,
        require_confirmed_transport=True,
    )

    bot.reconcile_runtime_historical_context_state()
    assert store.get(parent_id)["context_reply"]["state"] == (
        "context_reply_attempting"
    )
    result = bot.reconcile_confirmed_transactions_before_global_barrier(
        set(),
        set(),
        {},
    )

    assert result["historical_context"] is True
    context_reply = store.get(parent_id)["context_reply"]
    assert context_reply["state"] == "context_reply_confirmed"
    assert context_reply["reply_post_id"] == "980038"
    history_item = context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    ).history()["items"][parent_id]
    assert history_item["status"] == "completed"
    assert history_item["reply_post_id"] == "980038"
    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert not journal_path.exists()


def test_promoted_context_receipt_without_outbox_preserves_all_evidence() -> None:
    """Missing outbox authority cannot consume confirmed transport evidence."""

    parent_id = "800041"
    _store, source_receipt, journal_path = _seed_confirmed_context_transport(
        parent_id=parent_id,
        quote_id="2" * 64,
        reply_post_id="980041",
    )
    context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        mutation_authority_provider=bot.transaction_mutation_authority,
    ).promote_sending_receipt_from_confirmed_transport(
        source_receipt,
        reply_post_id="980041",
        confirmation_epoch=1_800_000_001,
        require_confirmed_transport=True,
    )
    bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.unlink()

    receipt_before = bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes()
    journal_before = journal_path.read_bytes()

    # Startup discovery deliberately defers this exact crash state to the
    # global pre-barrier reconciliation without mutating it.
    bot.reconcile_runtime_historical_context_state()
    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes() == receipt_before
    assert journal_path.read_bytes() == journal_before
    assert not bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.exists()

    with pytest.raises(
        RuntimeError,
        match="confirmed historical-context receipt has no matching outbox",
    ):
        bot.reconcile_confirmed_transactions_before_global_barrier(
            set(),
            set(),
            {},
        )

    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes() == receipt_before
    assert journal_path.read_bytes() == journal_before
    assert not bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.exists()


def test_confirmed_context_receipt_without_journal_still_requires_outbox() -> None:
    """Journal retirement cannot make a missing outbox safe to ignore."""

    parent_id = "800042"
    _store, source_receipt, journal_path = _seed_confirmed_context_transport(
        parent_id=parent_id,
        quote_id="3" * 64,
        reply_post_id="980042",
    )
    context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        mutation_authority_provider=bot.transaction_mutation_authority,
    ).promote_sending_receipt_from_confirmed_transport(
        source_receipt,
        reply_post_id="980042",
        confirmation_epoch=1_800_000_001,
        require_confirmed_transport=True,
    )
    # Model a crash/corruption boundary after journal retirement but before
    # the confirmed source receipt is consumed.  The surviving receipt still
    # requires its exact outbox authority.
    bot.fence_path_for_journal(journal_path).unlink()
    journal_path.unlink()
    bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.unlink()
    receipt_before = bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes()

    with pytest.raises(
        RuntimeError,
        match="confirmed historical-context receipt has no matching outbox",
    ):
        bot.reconcile_runtime_historical_context_state()

    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes() == receipt_before
    assert not bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.exists()


@pytest.mark.parametrize("entrypoint", ("startup", "prebarrier"))
def test_legacy_confirmed_context_receipt_rejects_failed_outbox(
    entrypoint: str,
) -> None:
    """Upgrade-era confirmed receipts cannot override a failed outbox."""

    parent_id = "800045"
    quote_id = "6" * 64
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_id,
        main_post_confirmed_epoch=1_800_000_000,
        quote_id=quote_id,
        quote_text="An upgrade-era confirmed context reply.",
    )
    store.claim_attempt(parent_id, started_epoch=1_800_000_001)
    store.record_retryable_failure(
        parent_id,
        attempt_number=1,
        error="contradictory pre-upgrade local failure",
        failed_epoch=1_800_000_002,
    )
    legacy_confirmed = {
        "schema_version": 1,
        "lifecycle_state": "confirmed",
        "parent_post_id": parent_id,
        "reply_post_id": "980045",
        "quote_id": quote_id,
        "reply_text": "Context — The legacy receipt proves confirmation.",
        "reply_epoch": 1_800_000_001,
        "started_at": "2026-08-01T12:00:01Z",
        "attempt_number": 1,
        "confirmed_at": "2026-08-01T12:00:02Z",
    }
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        legacy_confirmed,
    )
    receipt_before = bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes()
    outbox_before = bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.read_bytes()

    with pytest.raises(
        RuntimeError,
        match="confirmed historical-context receipt conflicts",
    ):
        if entrypoint == "startup":
            bot.reconcile_runtime_historical_context_state()
        else:
            bot.reconcile_confirmed_transactions_before_global_barrier(
                set(), set(), {}
            )

    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes() == receipt_before
    assert bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.read_bytes() == outbox_before
    assert not bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.exists()
    assert not bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.exists()


@pytest.mark.parametrize("entrypoint", ("startup", "prebarrier"))
def test_legacy_confirmed_context_receipt_confirms_attempting_outbox_first(
    entrypoint: str,
) -> None:
    """One consistent upgrade-era receipt advances its outbox before retirement."""

    parent_id = "800046"
    quote_id = "7" * 64
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_id,
        main_post_confirmed_epoch=1_800_000_000,
        quote_id=quote_id,
        quote_text="A consistent upgrade-era confirmed context reply.",
    )
    store.claim_attempt(parent_id, started_epoch=1_800_000_001)
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        {
            "schema_version": 1,
            "lifecycle_state": "confirmed",
            "parent_post_id": parent_id,
            "reply_post_id": "980046",
            "quote_id": quote_id,
            "reply_text": "Context — The legacy receipt is locally complete.",
            "reply_epoch": 1_800_000_001,
            "started_at": "2026-08-01T12:00:01Z",
            "attempt_number": 1,
            "confirmed_at": "2026-08-01T12:00:02Z",
        },
    )

    if entrypoint == "startup":
        bot.reconcile_runtime_historical_context_state()
    else:
        result = bot.reconcile_confirmed_transactions_before_global_barrier(
            set(), set(), {}
        )
        assert result["historical_context"] is True

    outcome = store.get(parent_id)["context_reply"]
    assert outcome["state"] == "context_reply_confirmed"
    assert outcome["reply_post_id"] == "980046"
    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    ).history()["items"][parent_id]["reply_post_id"] == "980046"


@pytest.mark.parametrize("entrypoint", ("startup", "prebarrier"))
def test_legacy_confirmed_context_reconciliation_rejects_receipt_replacement(
    entrypoint: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outbox advancement cannot authorise a later pathname generation."""

    parent_id = "800051"
    quote_id = "b" * 64
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_id,
        main_post_confirmed_epoch=1_800_000_000,
        quote_id=quote_id,
        quote_text="A legacy receipt subject to a local namespace race.",
    )
    store.claim_attempt(parent_id, started_epoch=1_800_000_001)
    original = {
        "schema_version": 1,
        "lifecycle_state": "confirmed",
        "parent_post_id": parent_id,
        "reply_post_id": "980051",
        "quote_id": quote_id,
        "reply_text": "Context — The initially inspected legacy receipt.",
        "reply_epoch": 1_800_000_001,
        "started_at": "2026-08-01T12:00:01Z",
        "attempt_number": 1,
        "confirmed_at": "2026-08-01T12:00:02Z",
    }
    replacement = {
        **original,
        "reply_post_id": "980052",
        "reply_text": "Context — A replacement receipt generation.",
    }
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        original,
    )
    real_record_confirmed = outbox_module.HistoricalContextOutbox.record_confirmed

    def replace_after_outbox_confirmation(self, *args, **kwargs):
        result = real_record_confirmed(self, *args, **kwargs)
        context_formatter.atomic_write_json(
            bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
            replacement,
        )
        return result

    monkeypatch.setattr(
        outbox_module.HistoricalContextOutbox,
        "record_confirmed",
        replace_after_outbox_confirmation,
    )

    with pytest.raises(RuntimeError, match="changed after its preloaded authority"):
        if entrypoint == "startup":
            bot.reconcile_runtime_historical_context_state()
        else:
            bot.reconcile_confirmed_transactions_before_global_barrier(
                set(), set(), {}
            )

    assert json.loads(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_text(encoding="utf-8")
    ) == replacement
    assert store.get(parent_id)["context_reply"]["reply_post_id"] == "980051"
    assert not bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.exists()


@pytest.mark.parametrize("entrypoint", ("startup", "prebarrier"))
def test_legacy_confirmed_context_requires_same_terminal_attempt(
    entrypoint: str,
) -> None:
    """Legacy parent/quote/reply identity cannot hide a later generation."""

    parent_id = "800052"
    quote_id = "e" * 64
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_id,
        main_post_confirmed_epoch=1_800_000_000,
        quote_id=quote_id,
        quote_text="A legacy terminal attempt identity fixture.",
    )
    store.claim_attempt(parent_id, started_epoch=1_800_000_001)
    store.record_confirmed(
        parent_id,
        attempt_number=1,
        reply_post_id="980052",
        confirmed_epoch=1_800_000_002,
    )
    receipt = {
        "schema_version": 1,
        "lifecycle_state": "confirmed",
        "parent_post_id": parent_id,
        "reply_post_id": "980052",
        "quote_id": quote_id,
        "reply_text": "Context — A different legacy source attempt.",
        "reply_epoch": 1_800_000_002,
        "started_at": "2026-08-01T12:00:01Z",
        "attempt_number": 2,
        "confirmed_at": "2026-08-01T12:00:02Z",
    }
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        receipt,
    )
    receipt_before = bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes()
    outbox_before = bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.read_bytes()

    with pytest.raises(
        RuntimeError,
        match="confirmed historical-context .*conflicts",
    ):
        if entrypoint == "startup":
            bot.reconcile_runtime_historical_context_state()
        else:
            bot.reconcile_confirmed_transactions_before_global_barrier(
                set(), set(), {}
            )

    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes() == receipt_before
    assert bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.read_bytes() == outbox_before


@pytest.mark.parametrize("state", ("terminal", "interrupted_retirement"))
def test_oldest_base_confirmed_receipt_remains_recoverable(state: str) -> None:
    """The oldest supported receipt has no ordinal or source-hash lineage."""

    parent_id = "800059"
    quote_id = "3" * 64
    outbox = bot.historical_context_outbox_store()
    outbox.enqueue(
        parent_id,
        main_post_confirmed_epoch=1_800_000_000,
        quote_id=quote_id,
        quote_text="An oldest-schema confirmed context receipt.",
    )
    outbox.claim_attempt(parent_id, started_epoch=1_800_000_001)
    receipt = {
        "schema_version": 1,
        "parent_post_id": parent_id,
        "reply_post_id": "980059",
        "quote_id": quote_id,
        "reply_text": "Context — This receipt predates attempt ordinals.",
        "reply_epoch": 1_800_000_001,
        "confirmed_at": "2026-08-01T12:00:02Z",
    }
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        receipt,
    )
    if state == "terminal":
        outbox.record_confirmed(
            parent_id,
            attempt_number=1,
            reply_post_id="980059",
            confirmed_epoch=1_800_000_002,
        )
        bot.reconcile_runtime_historical_context_state()
    else:
        context_formatter.atomic_write_json(
            bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
            {
                "schema_version": 1,
                "items": {parent_id: {**receipt, "status": "completed"}},
            },
        )
        receipt_bytes = bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes()
        bot.prepare_exact_receipt_retirement(
            bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
            receipt_bytes,
            mutation_authority=bot.transaction_mutation_authority(
                "focused oldest-schema context retirement preparation"
            ),
        )
        assert bot.resume_interrupted_source_receipt_retirement_if_present()

    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert outbox.get(parent_id)["context_reply"]["state"] == (
        "context_reply_confirmed"
    )
    assert context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    ).history()["items"][parent_id]["reply_post_id"] == "980059"


@pytest.mark.parametrize("entrypoint", ("startup", "prebarrier"))
def test_confirmed_context_receipt_requires_exact_terminal_outbox_lineage(
    entrypoint: str,
) -> None:
    """Same reply identity cannot authorise a different receipt generation."""

    parent_id = "800048"
    quote_id = "9" * 64
    store, source_a, journal_path = _seed_confirmed_context_transport(
        parent_id=parent_id,
        quote_id=quote_id,
        reply_post_id="980048",
    )
    context_store = context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        mutation_authority_provider=bot.transaction_mutation_authority,
    )
    context_store.promote_sending_receipt_from_confirmed_transport(
        source_a,
        reply_post_id="980048",
        confirmation_epoch=1_800_000_001,
        require_confirmed_transport=True,
    )
    store.record_confirmed(
        parent_id,
        attempt_number=1,
        reply_post_id="980048",
        confirmed_epoch=1_800_000_001,
    )
    bot.fence_path_for_journal(journal_path).unlink()
    journal_path.unlink()

    source_b = {
        **source_a,
        "reply_text": "Context — A different receipt generation.",
        "reply_epoch": 1_800_000_002,
        "attempt_number": 2,
    }
    confirmed_b = {
        **source_b,
        "lifecycle_state": "confirmed",
        "reply_post_id": "980048",
        "confirmed_at": "2026-08-01T12:00:03Z",
        "source_receipt_sha256": hashlib.sha256(
            context_formatter.canonical_json_bytes(source_b)
        ).hexdigest(),
    }
    assert context_formatter.HistoricalContextReplyStore._valid_receipt(
        confirmed_b
    )
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        confirmed_b,
    )
    receipt_before = bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes()
    outbox_before = bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.read_bytes()

    with pytest.raises(
        RuntimeError,
        match="confirmed historical-context .*conflicts",
    ):
        if entrypoint == "startup":
            bot.reconcile_runtime_historical_context_state()
        else:
            bot.reconcile_confirmed_transactions_before_global_barrier(
                set(), set(), {}
            )

    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes() == receipt_before
    assert bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.read_bytes() == outbox_before
    assert not bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.exists()


def test_interrupted_context_retirement_requires_outbox_before_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup cannot resume retirement before validating its outbox authority."""

    parent_id = "800047"
    store, source_receipt, journal_path = _seed_confirmed_context_transport(
        parent_id=parent_id,
        quote_id="8" * 64,
        reply_post_id="980047",
    )
    context_store = context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        mutation_authority_provider=bot.transaction_mutation_authority,
        retirement_uncertainty_callback=(
            bot.latch_source_receipt_retirement_uncertainty
        ),
    )
    context_store.promote_sending_receipt_from_confirmed_transport(
        source_receipt,
        reply_post_id="980047",
        confirmation_epoch=1_800_000_001,
        require_confirmed_transport=True,
    )
    monkeypatch.setattr(
        context_formatter,
        "retire_confirmed_transport_transaction",
        lambda **_kwargs: (_ for _ in ()).throw(
            OSError("injected journal retirement failure")
        ),
    )
    with pytest.raises(OSError, match="journal retirement"):
        context_store.reconcile_receipt_disposition()
    assert store.get(parent_id)["context_reply"]["state"] == (
        "context_reply_attempting"
    )
    bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.unlink()
    protected_paths = [
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        journal_path,
        bot.fence_path_for_journal(journal_path),
        *[
            path
            for path in bot.retirement_auxiliary_paths(
                bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
            )
            if path.exists()
        ],
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
    ]
    before = {path: path.read_bytes() for path in protected_paths}

    with pytest.raises(
        bot.ExactReceiptRetirementError,
        match="no matching outbox obligation",
    ):
        bot.reconcile_runtime_historical_context_state()

    assert {path: path.read_bytes() for path in protected_paths} == before


def test_interrupted_context_retirement_rejects_terminal_outbox_lineage_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retirement requires the terminal outbox's exact source generation."""

    parent_id = "800053"
    store, source_receipt, journal_path = _seed_confirmed_context_transport(
        parent_id=parent_id,
        quote_id="c" * 64,
        reply_post_id="980053",
    )
    context_store = context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        mutation_authority_provider=bot.transaction_mutation_authority,
        retirement_uncertainty_callback=(
            bot.latch_source_receipt_retirement_uncertainty
        ),
    )
    context_store.promote_sending_receipt_from_confirmed_transport(
        source_receipt,
        reply_post_id="980053",
        confirmation_epoch=1_800_000_001,
        require_confirmed_transport=True,
    )
    monkeypatch.setattr(
        context_formatter,
        "retire_confirmed_transport_transaction",
        lambda **_kwargs: (_ for _ in ()).throw(
            OSError("injected journal retirement failure")
        ),
    )
    with pytest.raises(OSError, match="journal retirement"):
        context_store.reconcile_receipt_disposition()
    store.record_confirmed(
        parent_id,
        attempt_number=1,
        reply_post_id="980053",
        confirmed_epoch=1_800_000_001,
    )
    outbox_document = json.loads(
        bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.read_text(encoding="utf-8")
    )
    outbox_document["obligations"][parent_id]["context_reply"][
        "source_receipt_attempt_number"
    ] += 1
    outbox_module._atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE,
        outbox_document,
    )
    protected_paths = [
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        journal_path,
        bot.fence_path_for_journal(journal_path),
        bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE,
        *[
            path
            for path in bot.retirement_auxiliary_paths(
                bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
            )
            if path.exists()
        ],
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
    ]
    before = {path: path.read_bytes() for path in protected_paths}

    with pytest.raises(
        bot.ExactReceiptRetirementError,
        match="durable outbox outcome",
    ):
        bot.reconcile_runtime_historical_context_state()

    assert {path: path.read_bytes() for path in protected_paths} == before


@pytest.mark.parametrize(
    ("terminal_state", "source_attempt_number"),
    (
        ("context_reply_failed_retryable", 1),
        ("context_reply_failed_terminal", 1),
        ("context_reply_failed_retryable", 21),
    ),
)
def test_interrupted_proved_failure_retirement_resumes_exactly(
    terminal_state: str,
    source_attempt_number: int,
) -> None:
    """A crash resumes exact failure even across independent attempt domains."""

    parent_id = str(800054 + source_attempt_number + int(terminal_state.endswith("terminal")))
    store = _seed_exact_failed_context_attempt(
        parent_id=parent_id,
        quote_id=("d" if terminal_state.endswith("retryable") else "e") * 64,
        reply_text="Context — The remote operation was proved not to occur.",
        source_attempt_number=source_attempt_number,
    )
    receipt_bytes = bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes()
    recorder = (
        store.record_retryable_failure
        if terminal_state.endswith("retryable")
        else store.record_terminal_failure
    )
    recorder(
        parent_id,
        attempt_number=1,
        error="RemoteOperationsPaused: stopped before transport",
        failed_epoch=8_200,
        proved_remote_non_success=True,
    )
    bot.prepare_exact_receipt_retirement(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        receipt_bytes,
        mutation_authority=bot.transaction_mutation_authority(
            "focused failed context retirement preparation"
        ),
    )

    assert bot.resume_interrupted_source_receipt_retirement_if_present() is True

    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert not any(
        os.path.lexists(path)
        for path in bot.retirement_auxiliary_paths(
            bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
        )
    )
    assert store.get(parent_id)["context_reply"]["state"] == terminal_state


def test_real_proved_failure_writer_recovers_interrupted_retirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real failure ordering produces the exact resumable proof triple."""

    parent_id = "800058"
    quote_id = "2" * 64
    reply_text = "Context — Local policy proved the request was not sent."
    outbox = bot.historical_context_outbox_store()
    outbox.enqueue(
        parent_id,
        main_post_confirmed_epoch=8_000,
        quote_id=quote_id,
        quote_text="A quotation with a proved pre-transport failure.",
    )
    outbox.claim_attempt(parent_id, started_epoch=8_100)
    reply_store = context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        mutation_authority_provider=bot.transaction_mutation_authority,
        retirement_uncertainty_callback=(
            bot.latch_source_receipt_retirement_uncertainty
        ),
    )
    real_retire = context_formatter.retire_or_resume_exact_receipt

    def prepare_then_interrupt(source_path, expected_bytes, **kwargs):
        bot.prepare_exact_receipt_retirement(
            source_path,
            expected_bytes,
            mutation_authority=kwargs["mutation_authority"],
        )
        raise OSError("injected post-prepare retirement interruption")

    monkeypatch.setattr(
        context_formatter,
        "retire_or_resume_exact_receipt",
        prepare_then_interrupt,
    )

    def definite_pretransport_failure(**kwargs):
        kwargs["on_remote_transaction_started"]()
        raise bot.RemoteOperationsPaused("paused before transport")

    with pytest.raises(
        context_formatter.DefiniteContextReplyLocalPersistenceError,
        match="clear context reply sending record",
    ):
        reply_store.post(
            parent_post_id=parent_id,
            quote_id=quote_id,
            reply_text=reply_text,
            create_post=definite_pretransport_failure,
            now_epoch=lambda: 1_800_000_000,
            on_source_receipt_published=(
                lambda source_sha256, source_attempt: (
                    outbox.bind_attempt_source_receipt(
                        parent_id,
                        attempt_number=1,
                        source_receipt_sha256=source_sha256,
                        source_receipt_attempt_number=source_attempt,
                    )
                )
            ),
            on_remote_transaction_started=(
                lambda: outbox.mark_remote_transaction_started(
                    parent_id,
                    attempt_number=1,
                )
            ),
            on_definite_non_success=(
                lambda error: bot._record_context_outbox_failure(
                    outbox,
                    parent_post_id=parent_id,
                    attempt_number=1,
                    error=error,
                    failed_epoch=1_800_000_001,
                    proved_remote_non_success=True,
                )
            ),
            remote_failure_is_definite_non_success=lambda error: isinstance(
                error, bot.RemoteOperationsPaused
            ),
        )

    assert outbox.get(parent_id)["context_reply"]["state"] == (
        "context_reply_failed_retryable"
    )
    assert reply_store.history()["items"][parent_id]["status"] == "failed"
    assert any(
        os.path.lexists(path)
        for path in bot.retirement_auxiliary_paths(
            bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
        )
    )

    monkeypatch.setattr(
        context_formatter,
        "retire_or_resume_exact_receipt",
        real_retire,
    )
    bot.reconcile_runtime_historical_context_state()

    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert not any(
        os.path.lexists(path)
        for path in bot.retirement_auxiliary_paths(
            bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
        )
    )


@pytest.mark.parametrize("mismatch", ("history", "outbox", "marker"))
def test_interrupted_proved_failure_retirement_rejects_mismatched_authority(
    mismatch: str,
) -> None:
    """Marker, failed history and outbox must bind the same exact source."""

    parent_id = "800056"
    store = _seed_exact_failed_context_attempt(
        parent_id=parent_id,
        quote_id="f" * 64,
        reply_text="Context — Every local proof must name the same source.",
    )
    receipt_bytes = bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes()
    store.record_retryable_failure(
        parent_id,
        attempt_number=1,
        error="RemoteOperationsPaused: stopped before transport",
        failed_epoch=8_200,
        proved_remote_non_success=True,
    )
    if mismatch == "marker":
        source = json.loads(receipt_bytes)
        source["reply_text"] += " changed"
        context_formatter.atomic_write_json(
            bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
            source,
        )
        receipt_bytes = bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes()
    bot.prepare_exact_receipt_retirement(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        receipt_bytes,
        mutation_authority=bot.transaction_mutation_authority(
            "focused mismatched failed context retirement preparation"
        ),
    )
    if mismatch == "history":
        history = json.loads(
            bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.read_text(encoding="utf-8")
        )
        history["items"][parent_id]["source_receipt_sha256"] = "0" * 64
        context_formatter.atomic_write_json(
            bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
            history,
        )
    elif mismatch == "outbox":
        document = json.loads(
            bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.read_text(encoding="utf-8")
        )
        document["obligations"][parent_id]["context_reply"]["failure"][
            "source_receipt_sha256"
        ] = "0" * 64
        outbox_module._atomic_write_json(
            bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE,
            document,
        )
    protected = {
        path: path.read_bytes()
        for path in (
            bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
            bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
            bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE,
            *bot.retirement_auxiliary_paths(
                bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
            ),
        )
        if path.exists()
    }

    with pytest.raises(bot.ExactReceiptRetirementError):
        bot.resume_interrupted_source_receipt_retirement_if_present()

    assert {path: path.read_bytes() for path in protected} == protected


def test_source_binding_callback_failure_retains_pretransport_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local binding fault leaves exact authority for restart recovery."""

    parent_id = "800057"
    quote_id = "1" * 64
    reply_text = "Context — Binding failed before transport could begin."
    outbox = bot.historical_context_outbox_store()
    outbox.enqueue(
        parent_id,
        main_post_confirmed_epoch=8_000,
        quote_id=quote_id,
        quote_text="A quotation whose context receipt remains recoverable.",
    )
    outbox.claim_attempt(parent_id, started_epoch=8_100)
    reply_store = context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        mutation_authority_provider=bot.transaction_mutation_authority,
        retirement_uncertainty_callback=(
            bot.latch_source_receipt_retirement_uncertainty
        ),
    )

    with pytest.raises(
        context_formatter.DefiniteContextReplyLocalPersistenceError,
        match="exact pre-transport receipt was retained",
    ):
        reply_store.post(
            parent_post_id=parent_id,
            quote_id=quote_id,
            reply_text=reply_text,
            create_post=_forbid("transport after failed source binding"),
            now_epoch=lambda: 1_800_000_000,
            on_source_receipt_published=lambda *_args: (_ for _ in ()).throw(
                OSError("injected source-binding persistence failure")
            ),
        )

    retained = bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes()
    assert json.loads(retained)["lifecycle_state"] == "sending"
    assert not bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.exists()
    assert not bot.journal_path_for_receipt(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
    ).exists()
    assert not any(
        os.path.lexists(path)
        for path in bot.retirement_auxiliary_paths(
            bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
        )
    )

    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_001)
    bot.reconcile_runtime_historical_context_state()

    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    failed = outbox.get(parent_id)["context_reply"]
    assert failed["state"] == "context_reply_failed_retryable"
    assert failed["failure"]["remote_outcome"] == "proved_non_success"
    history = reply_store.history()["items"][parent_id]
    assert history["status"] == "failed"
    assert history["source_receipt_sha256"] == hashlib.sha256(retained).hexdigest()


def test_worker_never_recasts_confirmed_transport_as_outbox_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local post-confirmation error preserves the confirmed outbox."""

    parent_id = "800039"
    quote_id = "0" * 64
    reply_text = "Context — Remote confirmation precedes local completion."
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_id,
        main_post_confirmed_epoch=1_800_000_000,
        quote_id=quote_id,
        quote_text="A confirmed context reply with interrupted local state.",
    )

    def confirmed_then_local_failure(**callbacks):
        source_receipt = {
            "schema_version": 1,
            "lifecycle_state": "sending",
            "parent_post_id": parent_id,
            "quote_id": quote_id,
            "reply_text": reply_text,
            "reply_epoch": 1_800_000_000,
            "started_at": "2026-08-01T12:00:00Z",
            "attempt_number": 1,
        }
        context_formatter.atomic_write_json(
            bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
            source_receipt,
        )
        source_bytes = context_formatter.canonical_json_bytes(source_receipt)
        callbacks["on_source_receipt_published"](
            hashlib.sha256(source_bytes).hexdigest(),
            1,
        )
        payload = {
            "text": reply_text,
            "reply": {"in_reply_to_tweet_id": parent_id},
        }
        binding = bot.bind_lane_transport_source(
            receipt_path=bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
            receipt=source_receipt,
            lane="historical_context_reply",
            payload=payload,
        )
        authority = bot.begin_transport_transaction(
            receipt_path=bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
            source_binding=binding,
        )
        journal_path = bot.journal_path_for_receipt(
            bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
        )
        authority = bot.arm_transport_transaction(
            journal_path,
            authority,
            mutation_authority=bot.transaction_mutation_authority(
                "focused worker journal arming"
            ),
        )
        callbacks["on_remote_transaction_started"]()
        bot.consume_transport_authority(
            journal_path,
            authority,
            method="POST",
            request_path="/2/tweets",
            payload=payload,
            expected_receipt_path=(
                bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
            ),
        )
        bot.confirm_transport_transaction(
            journal_path,
            authority,
            mutation_authority=bot.transaction_mutation_authority(
                "focused worker journal confirmation"
            ),
            post_id="980039",
            confirmation_epoch=1_800_000_001,
        )
        confirmed_receipt = context_formatter.HistoricalContextReplyStore(
            bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
            bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
            mutation_authority_provider=bot.transaction_mutation_authority,
        ).promote_sending_receipt_from_confirmed_transport(
            source_receipt,
            reply_post_id="980039",
            confirmation_epoch=1_800_000_001,
            require_confirmed_transport=True,
        )
        callbacks["on_confirmed_receipt"](
            confirmed_receipt,
            1_800_000_001,
        )
        raise OSError("synthetic history directory fsync interruption")

    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        confirmed_then_local_failure,
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)

    result = bot.process_due_historical_context_obligations(
        parent_post_id=parent_id,
    )

    assert result == [
        {
            "parent_post_id": parent_id,
            "status": "failed",
            "context_reply_state": (
                "confirmed_local_reconciliation_pending"
            ),
        }
    ]
    context_reply = store.get(parent_id)["context_reply"]
    assert context_reply["state"] == "context_reply_confirmed"
    assert context_reply["reply_post_id"] == "980039"
    assert not bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.exists()

    recovered = bot.reconcile_confirmed_transactions_before_global_barrier(
        set(),
        set(),
        {},
    )

    assert recovered["historical_context"] is True
    assert store.get(parent_id)["context_reply"]["state"] == (
        "context_reply_confirmed"
    )
    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()


def test_real_context_worker_forwards_confirmed_receipt_before_history_retirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise worker -> formatter -> store, not a hand-invoked callback."""

    from historical_context_source_roles import POLICY_VERSION

    parent_id = "800049"
    quote_id = "a" * 64
    reply_text = "Context — Confirmation is durable before local completion."
    outbox = bot.historical_context_outbox_store()
    outbox.enqueue(
        parent_id,
        main_post_confirmed_epoch=1_800_000_000,
        quote_id=quote_id,
        quote_text="A complete worker-to-store callback fixture.",
    )
    packet = {"quote_id": quote_id, "quote_text": "Canonical quotation."}
    formatted = {
        "text": reply_text,
        "formatter_version": context_formatter.HISTORICAL_CONTEXT_FORMATTER_V5,
        "template_variant": "test_confirmed_callback_forwarding",
        "meaning_included": False,
        "meaning_decision_reason": "Focused transaction fixture.",
        "raw_character_count": len(reply_text),
        "character_count": len(reply_text),
        "weighted_character_count": len(reply_text),
        "maximum_length": 4_000,
        "verification_label": "Exact wording",
        "source_class": "primary",
        "historical_confidence": "high",
        "confidence_dimensions": {
            name: "high"
            for name in (
                "attribution",
                "wording",
                "source_event",
                "date",
                "historical_context",
                "interpretation",
            )
        },
        "source_role_audit_version": POLICY_VERSION,
        "rendering_mode": context_formatter.PUBLIC_RENDERING_MODE,
        "shortening_applied": False,
    }
    gate = SimpleNamespace(
        available=True,
        ledger_sha256="a" * 64,
        projection_sha256="b" * 64,
        disposition=lambda _quote_id: None,
        reviewed_disposition=lambda _quote_id: None,
    )
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT",
        ({quote_id: packet}, set()),
    )
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_SEMANTIC_GATE", gate)
    monkeypatch.setattr(
        context_formatter,
        "packet_for_posted_quote",
        lambda *_args, **_kwargs: packet,
    )
    monkeypatch.setattr(
        context_formatter,
        "format_context_reply_public",
        lambda *_args, **_kwargs: formatted,
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_010)

    def local_transport(
        method,
        path,
        *,
        json,
        _remote_write_authorization,
        **_kwargs,
    ):
        assert (method, path) == ("POST", "/2/tweets")
        bot.consume_transport_authority(
            Path(_remote_write_authorization.journal_path),
            _remote_write_authorization,
            method=method,
            request_path=path,
            payload=json,
            expected_receipt_path=bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        )
        return {"data": {"id": "980049"}}

    real_save_history = context_formatter.HistoricalContextReplyStore._save_history

    def fail_completed_history(self, value):
        item = value.get("items", {}).get(parent_id)
        if isinstance(item, dict) and item.get("status") == "completed":
            confirmed = outbox.get(parent_id)["context_reply"]
            assert confirmed["state"] == "context_reply_confirmed"
            assert confirmed["reply_post_id"] == "980049"
            assert confirmed["source_receipt_sha256"] == item[
                "source_receipt_sha256"
            ]
            assert confirmed["source_receipt_attempt_number"] == item[
                "attempt_number"
            ]
            raise OSError("injected completed-history persistence failure")
        return real_save_history(self, value)

    monkeypatch.setattr(bot, "x_request", local_transport)
    monkeypatch.setattr(bot, "create_post", _REAL_CREATE_POST)
    monkeypatch.setattr(
        context_formatter.HistoricalContextReplyStore,
        "_save_history",
        fail_completed_history,
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id=parent_id,
    )

    assert result == [
        {
            "parent_post_id": parent_id,
            "status": "failed",
            "context_reply_state": "confirmed_local_reconciliation_pending",
        }
    ]
    confirmed = outbox.get(parent_id)["context_reply"]
    assert confirmed["state"] == "context_reply_confirmed"
    assert confirmed["reply_post_id"] == "980049"
    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert not bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.exists()


def test_real_context_worker_recovers_confirmed_callback_persistence_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed outbox callback retains exact confirmed recovery authority."""

    parent_id = "800054"
    outbox = _install_real_context_worker_success_fixture(
        monkeypatch,
        parent_id=parent_id,
        quote_id="d" * 64,
        reply_text="Context — The confirmation callback failed locally.",
        reply_post_id="980054",
    )
    real_record_confirmed = outbox_module.HistoricalContextOutbox.record_confirmed
    attempts = {"count": 0}

    def fail_first_confirmation(self, *args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise OSError("injected outbox confirmation persistence failure")
        return real_record_confirmed(self, *args, **kwargs)

    monkeypatch.setattr(
        outbox_module.HistoricalContextOutbox,
        "record_confirmed",
        fail_first_confirmation,
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id=parent_id,
    )

    assert result == [
        {
            "parent_post_id": parent_id,
            "status": "failed",
            "context_reply_state": "confirmed_local_reconciliation_pending",
        }
    ]
    attempting = outbox.get(parent_id)["context_reply"]
    assert attempting["state"] == "context_reply_attempting"
    assert attempting["remote_transaction_started"] is True
    assert attempting["source_receipt_sha256"]
    receipt = context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    )._load_receipt_safely()
    assert receipt is not None
    assert receipt[0]["lifecycle_state"] == "confirmed"
    journal_path = bot.journal_path_for_receipt(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
    )
    assert bot.inspect_transport_state(journal_path).classification == (
        "confirmed_pair"
    )
    assert not bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.exists()

    recovered = bot.reconcile_confirmed_transactions_before_global_barrier(
        set(), set(), {}
    )

    assert recovered["historical_context"] is True
    confirmed = outbox.get(parent_id)["context_reply"]
    assert confirmed["state"] == "context_reply_confirmed"
    assert confirmed["reply_post_id"] == "980054"
    assert confirmed["source_receipt_sha256"] == attempting[
        "source_receipt_sha256"
    ]
    assert attempts["count"] == 2
    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert not journal_path.exists()


def test_concurrent_context_worker_cannot_claim_a_second_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    for parent_id, quote_id in (("800020", "1" * 64), ("800021", "2" * 64)):
        store.enqueue(
            parent_id,
            main_post_confirmed_epoch=8_000,
            quote_id=quote_id,
            quote_text=f"Concurrent worker fixture {parent_id}.",
        )
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("second worker remote work"),
    )

    with store.worker_lock():
        assert bot.process_due_historical_context_obligations(limit=2) == []

    assert store.get("800020")["context_reply"]["state"] == "context_reply_pending"
    assert store.get("800021")["context_reply"]["state"] == "context_reply_pending"


def test_due_context_claim_survives_clock_rollback_between_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A due row remains claimable when the next wall-clock read moves back."""

    parent_id = "800050"
    due_epoch = 1_800_000_400
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_id,
        main_post_confirmed_epoch=due_epoch,
        quote_id="b" * 64,
        quote_text="A due context obligation across clock rollback.",
    )
    observations = iter((due_epoch, due_epoch - 1, due_epoch - 1))
    monkeypatch.setattr(bot, "now_epoch", lambda: next(observations))
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **_kwargs: {"status": "disabled"},
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id=parent_id,
    )

    assert result == [
        {
            "parent_post_id": parent_id,
            "status": "disabled",
            "context_reply_state": "context_reply_not_required",
        }
    ]
    context = store.get(parent_id)["context_reply"]
    assert context["state"] == "context_reply_not_required"
    assert context["updated_epoch"] == due_epoch
    assert bot._HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON is None


def test_interrupted_claim_is_recovered_with_backoff_after_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800008",
        main_post_confirmed_epoch=8_000,
        quote_id="7" * 64,
        quote_text="An interrupted context obligation.",
    )
    claimed = store.claim_attempt("800008", started_epoch=8_100)
    assert claimed["context_reply"]["state"] == "context_reply_attempting"
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {
            "schema_version": 1,
            "items": {
                "800008": {
                    "parent_post_id": "800008",
                    "quote_id": "7" * 64,
                    "reply_text": "Rendered context text.",
                    "status": "failed",
                    "failure": "TimeoutError: definite pre-send failure",
                    "attempt_count": 1,
                    "updated_at": "2026-07-24T00:00:00Z",
                }
            },
        },
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_200)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("remote work for an interrupted claimed attempt"),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800008",
    )

    assert result == [
        {
            "parent_post_id": "800008",
            "status": "recovered_pre_remote_interruption",
            "context_reply_state": "context_reply_failed_retryable",
            "attempt_number": 1,
            "remote_work_repeated": False,
        }
    ]
    recovered = store.get("800008")["context_reply"]
    assert recovered["attempt_count"] == 1
    assert recovered["next_attempt_epoch"] == 8_260
    assert "interrupted before remote transaction start" in recovered["failure"]["error"]
    assert bot.process_due_historical_context_obligations(
        parent_post_id="800008",
    ) == []


def test_interrupted_pre_remote_context_claim_becomes_retryable_without_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A current claim interrupted before transport cannot have posted remotely."""

    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800017",
        main_post_confirmed_epoch=8_000,
        quote_id="5" * 64,
        quote_text="A context attempt interrupted before remote preparation.",
    )
    claimed = store.claim_attempt("800017", started_epoch=8_100)
    assert claimed["context_reply"]["remote_transaction_started"] is False
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_200)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("repeating work while recovering the pre-remote claim"),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800017",
    )

    assert result == [
        {
            "parent_post_id": "800017",
            "status": "recovered_pre_remote_interruption",
            "context_reply_state": "context_reply_failed_retryable",
            "attempt_number": 1,
            "remote_work_repeated": False,
        }
    ]
    recovered = store.get("800017")["context_reply"]
    assert recovered["state"] == "context_reply_failed_retryable"
    assert "before remote transaction start" in recovered["failure"]["error"]
    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
    assert bot.ambiguous_remote_post_is_blocking() is False


def test_bound_source_receipt_before_journal_recovers_as_pre_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exact bound source with no journal still proves transport never began."""

    parent_id = "800044"
    quote_id = "5" * 64
    durable_epoch = 1_800_000_100
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_id,
        main_post_confirmed_epoch=durable_epoch - 10,
        quote_id=quote_id,
        quote_text="A crash after source publication but before journal arming.",
    )
    store.claim_attempt(parent_id, started_epoch=durable_epoch)
    source_receipt = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": parent_id,
        "quote_id": quote_id,
        "reply_text": "Context — The source was durable before transport.",
        "reply_epoch": durable_epoch,
        "started_at": "2026-08-01T12:01:40Z",
        "attempt_number": 21,
    }
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        source_receipt,
    )
    _bind_context_attempt_to_source_receipt(
        store,
        parent_id=parent_id,
        outbox_attempt=1,
        source_receipt=source_receipt,
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: durable_epoch - 1)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("remote work after a proved pre-transport crash"),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id=parent_id,
    )

    assert result == [
        {
            "parent_post_id": parent_id,
            "status": "recovered_pre_remote_interruption",
            "context_reply_state": "context_reply_failed_retryable",
            "attempt_number": 1,
            "remote_work_repeated": False,
        }
    ]
    recovered = store.get(parent_id)["context_reply"]
    assert recovered["failure"]["failed_epoch"] == durable_epoch
    assert recovered["failure"]["source_receipt_attempt_number"] == 21
    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    history = context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    ).history()["items"][parent_id]
    assert history["attempt_count"] == 21
    assert history["remote_outcome"] == "proved_non_success"


def test_unbound_source_receipt_before_callback_recovers_as_pre_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hard crash after publication but before its bind callback is local."""

    parent_id = "800048"
    quote_id = "9" * 64
    durable_epoch = 1_800_000_200
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_id,
        main_post_confirmed_epoch=durable_epoch - 10,
        quote_id=quote_id,
        quote_text="A crash between source publication and binding callback.",
    )
    store.claim_attempt(parent_id, started_epoch=durable_epoch)
    source_receipt = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": parent_id,
        "quote_id": quote_id,
        "reply_text": "Context — Publication preceded the callback crash.",
        "reply_epoch": durable_epoch,
        "started_at": "2026-08-01T12:03:20Z",
        "attempt_number": 1,
    }
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        source_receipt,
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: durable_epoch + 1)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("remote work after the publication-to-callback crash"),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id=parent_id,
    )

    assert result[0]["status"] == "recovered_pre_remote_interruption"
    recovered = store.get(parent_id)["context_reply"]
    assert recovered["state"] == "context_reply_failed_retryable"
    assert recovered["failure"]["source_receipt_attempt_number"] == 1
    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    ).history()["items"][parent_id]["attempt_count"] == 1


def test_already_completed_crash_confirms_newly_claimed_outbox() -> None:
    """A crash after already_completed cannot recast confirmed history as failure."""

    parent_id = "800049"
    quote_id = "a" * 64
    durable_epoch = 1_800_000_300
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_id,
        main_post_confirmed_epoch=durable_epoch - 10,
        quote_id=quote_id,
        quote_text="A reply already present in completed history.",
    )
    store.claim_attempt(parent_id, started_epoch=durable_epoch)
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {
            "schema_version": 1,
            "items": {
                parent_id: {
                    "schema_version": 1,
                    "parent_post_id": parent_id,
                    "reply_post_id": "980049",
                    "quote_id": quote_id,
                    "reply_text": "Context — This reply was already completed.",
                    "reply_epoch": durable_epoch - 20,
                    "confirmed_at": "legacy-writer-time",
                    "status": "completed",
                }
            },
        },
    )

    recovered = bot.recover_interrupted_historical_context_attempt(
        store,
        store.get(parent_id),
        recovered_epoch=durable_epoch + 1,
    )

    assert recovered["status"] == "recovered_confirmed_history"
    context = store.get(parent_id)["context_reply"]
    assert context["state"] == "context_reply_confirmed"
    assert context["reply_post_id"] == "980049"
    assert context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    ).history()["items"][parent_id]["status"] == "completed"


def test_pre_remote_claim_with_transport_journal_stays_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A journal or unsafe journal object overrides the local-only phase."""

    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800016",
        main_post_confirmed_epoch=8_000,
        quote_id="4" * 64,
        quote_text="An interrupted claim with independent transport evidence.",
    )
    store.claim_attempt("800016", started_epoch=8_100)
    journal_path = bot.journal_path_for_receipt(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
    )
    journal_path.write_bytes(b"invalid journal sentinel\n")
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_200)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("repeating a claim while its transport journal is unresolved"),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800016",
    )

    assert result == []
    assert (
        store.get("800016")["context_reply"]["state"]
        == "context_reply_attempting"
    )
    assert journal_path.exists()
    assert bot.ambiguous_remote_post_is_blocking() is True


def test_interrupted_remote_started_context_claim_without_history_stays_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loss of the sole completed-history record cannot make a reply retryable."""

    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800018",
        main_post_confirmed_epoch=8_000,
        quote_id="6" * 64,
        quote_text="A context reply whose completed history was lost.",
    )
    store.claim_attempt("800018", started_epoch=8_100)
    store.bind_attempt_source_receipt(
        "800018",
        attempt_number=1,
        source_receipt_sha256="1" * 64,
        source_receipt_attempt_number=1,
    )
    store.mark_remote_transaction_started("800018", attempt_number=1)
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {
            "schema_version": 1,
            "items": {
                "800018": {
                    "schema_version": 1,
                    "parent_post_id": "800018",
                    "reply_post_id": "900018",
                    "quote_id": "6" * 64,
                    "reply_text": "Rendered context text.",
                    "reply_epoch": 1_800_000_101,
                    "confirmed_at": "2026-07-24T00:00:00Z",
                    "status": "completed",
                }
            },
        },
    )
    bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.unlink()
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_200)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("repeating a context reply without terminal history"),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800018",
    )

    assert result == [
        {
            "parent_post_id": "800018",
            "status": "interrupted_attempt_recovery_failed",
            "error_type": "AmbiguousContextReplyOutcome",
        }
    ]
    assert (
        store.get("800018")["context_reply"]["state"]
        == "context_reply_attempting"
    )
    assert bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.block_if_ambiguous_remote_post()


def test_legacy_interrupted_context_claim_without_phase_stays_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-phase schema-v1 claim cannot be assumed to precede transmission."""

    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800019",
        main_post_confirmed_epoch=8_000,
        quote_id="7" * 64,
        quote_text="A legacy context attempt with an unknown remote phase.",
    )
    store.claim_attempt("800019", started_epoch=8_100)
    document = json.loads(
        bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.read_text(encoding="utf-8")
    )
    document["obligations"]["800019"]["context_reply"].pop(
        "remote_transaction_started"
    )
    outbox_module._atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE,
        document,
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_200)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("repeating a legacy attempt with unknown transmission state"),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800019",
    )

    assert result[0]["status"] == "interrupted_attempt_recovery_failed"
    assert result[0]["error_type"] == "AmbiguousContextReplyOutcome"
    assert (
        store.get("800019")["context_reply"]["state"]
        == "context_reply_attempting"
    )
    assert bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()


def test_interrupted_claim_does_not_compare_distinct_store_attempt_ordinals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800010",
        main_post_confirmed_epoch=8_000,
        quote_id="9" * 64,
        quote_text="Preparation failed before the reply store saw attempt one.",
    )
    store.claim_attempt("800010", started_epoch=8_000)
    store.record_retryable_failure(
        "800010",
        attempt_number=1,
        error="formatter failed before remote transaction",
        failed_epoch=8_100,
    )
    claimed = store.claim_attempt("800010", started_epoch=8_200)
    assert claimed["context_reply"]["attempt_count"] == 2
    assert claimed["context_reply"]["remote_transaction_started"] is False
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {
            "schema_version": 1,
            "items": {
                "800010": {
                    "parent_post_id": "800010",
                    "quote_id": "9" * 64,
                    "reply_text": "Rendered context text.",
                    "status": "failed",
                    "failure": "TimeoutError: first reply-store attempt failed",
                    "attempt_count": 1,
                    "updated_at": "2026-07-24T00:00:00Z",
                }
            },
        },
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_300)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("repeating interrupted remote work"),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800010",
    )

    assert result[0]["status"] == "recovered_pre_remote_interruption"
    recovered = store.get("800010")["context_reply"]
    assert recovered["state"] == "context_reply_failed_retryable"
    assert recovered["attempt_count"] == 2
    assert "interrupted before remote transaction start" in recovered["failure"]["error"]


def test_remote_started_claim_cannot_consume_stale_failed_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An older failed row cannot prove a later transmitted attempt failed."""

    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800020",
        main_post_confirmed_epoch=8_000,
        quote_id="a" * 64,
        quote_text="A later remote-started attempt with stale failure history.",
    )
    store.claim_attempt("800020", started_epoch=8_000)
    store.record_retryable_failure(
        "800020",
        attempt_number=1,
        error="older definite failure",
        failed_epoch=8_100,
    )
    store.claim_attempt("800020", started_epoch=8_200)
    store.bind_attempt_source_receipt(
        "800020",
        attempt_number=2,
        source_receipt_sha256="2" * 64,
        source_receipt_attempt_number=2,
    )
    store.mark_remote_transaction_started("800020", attempt_number=2)
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        {
            "schema_version": 1,
            "lifecycle_state": "sending",
            "parent_post_id": "800020",
            "quote_id": "a" * 64,
            "reply_text": "Rendered context text.",
            "reply_epoch": 1_800_000_000,
            "started_at": "2026-08-01T00:00:00Z",
            "attempt_number": 1,
        },
    )
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {
            "schema_version": 1,
            "items": {
                "800020": {
                    "parent_post_id": "800020",
                    "quote_id": "a" * 64,
                    "reply_text": "Rendered context text.",
                    "status": "failed",
                    "failure": "TimeoutError: older first attempt",
                    "attempt_count": 1,
                    "updated_at": "2026-08-01T00:00:00Z",
                }
            },
        },
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_300)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("repeating remote-started work with only stale failure history"),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800020",
    )

    assert result == [
        {
            "parent_post_id": "800020",
            "status": "interrupted_attempt_recovery_failed",
            "error_type": "AmbiguousContextReplyOutcome",
        }
    ]
    assert (
        store.get("800020")["context_reply"]["state"]
        == "context_reply_attempting"
    )
    assert bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()


def test_remote_started_claim_consumes_exact_failed_sending_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exact same-attempt failure receipt proves definite non-success."""

    parent_id = "800023"
    quote_id = "d" * 64
    reply_text = "Context — The local pause prevented transmission."
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_id,
        main_post_confirmed_epoch=8_000,
        quote_id=quote_id,
        quote_text="A context attempt with exact failure evidence.",
    )
    store.claim_attempt(parent_id, started_epoch=8_100)
    source_receipt = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": parent_id,
        "quote_id": quote_id,
        "reply_text": reply_text,
        "reply_epoch": 1_800_000_000,
        "started_at": "2026-08-01T12:00:00Z",
        "attempt_number": 1,
    }
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        source_receipt,
    )
    source_sha256 = _bind_context_attempt_to_source_receipt(
        store,
        parent_id=parent_id,
        outbox_attempt=1,
        source_receipt=source_receipt,
    )
    store.mark_remote_transaction_started(parent_id, attempt_number=1)
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {
            "schema_version": 1,
            "items": {
                parent_id: _proved_failure_history_item(
                    source_receipt=source_receipt,
                    source_sha256=source_sha256,
                )
            },
        },
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_200)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("repeating an exactly failed historical-context attempt"),
    )

    bot.reconcile_runtime_historical_context_state()

    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert store.get(parent_id)["context_reply"]["state"] == (
        "context_reply_failed_retryable"
    )
    assert bot.process_due_historical_context_obligations(
        parent_post_id=parent_id,
    ) == []
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()


def test_exact_failed_context_receipt_cannot_override_transport_journal() -> None:
    """Independent transport evidence keeps an exact failed row blocked."""

    parent_id = "800024"
    quote_id = "e" * 64
    reply_text = "Context — Failure evidence conflicts with a journal."
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_id,
        main_post_confirmed_epoch=8_000,
        quote_id=quote_id,
        quote_text="A context attempt with conflicting transport evidence.",
    )
    store.claim_attempt(parent_id, started_epoch=8_100)
    source_receipt = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": parent_id,
        "quote_id": quote_id,
        "reply_text": reply_text,
        "reply_epoch": 1_800_000_000,
        "started_at": "2026-08-01T12:00:00Z",
        "attempt_number": 1,
    }
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        source_receipt,
    )
    source_sha256 = _bind_context_attempt_to_source_receipt(
        store,
        parent_id=parent_id,
        outbox_attempt=1,
        source_receipt=source_receipt,
    )
    store.mark_remote_transaction_started(parent_id, attempt_number=1)
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {
            "schema_version": 1,
            "items": {
                parent_id: _proved_failure_history_item(
                    source_receipt=source_receipt,
                    source_sha256=source_sha256,
                )
            },
        },
    )
    journal_path = bot.journal_path_for_receipt(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
    )
    journal_path.write_bytes(b"unresolved transport evidence\n")

    with pytest.raises(
        context_formatter.AmbiguousContextReplyOutcome,
        match="failure history conflicts with an unresolved transport journal",
    ):
        bot.recover_interrupted_historical_context_attempt(
            store,
            store.get(parent_id),
            recovered_epoch=8_200,
            receipt_was_observed=True,
        )

    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert journal_path.exists()
    assert store.get(parent_id)["context_reply"]["state"] == (
        "context_reply_attempting"
    )


def test_exact_failed_context_receipt_survives_outbox_recovery_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The receipt remains until the exact outbox failure is durable."""

    parent_id = "800025"
    quote_id = "f" * 64
    reply_text = "Context — Local failure evidence remains restart-safe."
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_id,
        main_post_confirmed_epoch=8_000,
        quote_id=quote_id,
        quote_text="A context attempt whose outbox write is interrupted.",
    )
    store.claim_attempt(parent_id, started_epoch=8_100)
    source_receipt = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": parent_id,
        "quote_id": quote_id,
        "reply_text": reply_text,
        "reply_epoch": 1_800_000_000,
        "started_at": "2026-08-01T12:00:00Z",
        "attempt_number": 1,
    }
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        source_receipt,
    )
    source_sha256 = _bind_context_attempt_to_source_receipt(
        store,
        parent_id=parent_id,
        outbox_attempt=1,
        source_receipt=source_receipt,
    )
    store.mark_remote_transaction_started(parent_id, attempt_number=1)
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {
            "schema_version": 1,
            "items": {
                parent_id: _proved_failure_history_item(
                    source_receipt=source_receipt,
                    source_sha256=source_sha256,
                )
            },
        },
    )
    real_record_failure = bot._record_context_outbox_failure
    monkeypatch.setattr(
        bot,
        "_record_context_outbox_failure",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("outbox directory fsync interrupted")
        ),
    )

    with pytest.raises(OSError, match="outbox directory fsync interrupted"):
        bot.reconcile_runtime_historical_context_state()

    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert store.get(parent_id)["context_reply"]["state"] == (
        "context_reply_attempting"
    )

    monkeypatch.setattr(bot, "_record_context_outbox_failure", real_record_failure)
    bot.reconcile_runtime_historical_context_state()

    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert store.get(parent_id)["context_reply"]["state"] == (
        "context_reply_failed_retryable"
    )
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()


def test_unpaused_prebarrier_tick_recovers_paused_exact_context_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exact local failure left by maintenance pause resumes locally."""

    parent_id = "800026"
    quote_id = "1" * 64
    reply_text = "Context — Maintenance deferred local reconciliation."
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_id,
        main_post_confirmed_epoch=8_000,
        quote_id=quote_id,
        quote_text="A context attempt paused across startup.",
    )
    store.claim_attempt(parent_id, started_epoch=8_100)
    source_receipt = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": parent_id,
        "quote_id": quote_id,
        "reply_text": reply_text,
        "reply_epoch": 1_800_000_000,
        "started_at": "2026-08-01T12:00:00Z",
        "attempt_number": 1,
    }
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        source_receipt,
    )
    source_sha256 = _bind_context_attempt_to_source_receipt(
        store,
        parent_id=parent_id,
        outbox_attempt=1,
        source_receipt=source_receipt,
    )
    store.mark_remote_transaction_started(parent_id, attempt_number=1)
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {
            "schema_version": 1,
            "items": {
                parent_id: _proved_failure_history_item(
                    source_receipt=source_receipt,
                    source_sha256=source_sha256,
                )
            },
        },
    )
    paused = {"value": True}
    monkeypatch.setattr(
        bot,
        "global_remote_writes_paused",
        lambda: paused["value"],
    )

    bot.reconcile_runtime_historical_context_state()

    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert store.get(parent_id)["context_reply"]["state"] == (
        "context_reply_attempting"
    )

    paused["value"] = False
    result = bot.reconcile_confirmed_transactions_before_global_barrier(
        set(),
        set(),
        {},
    )

    assert result["historical_context"] is True
    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert store.get(parent_id)["context_reply"]["state"] == (
        "context_reply_failed_retryable"
    )
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()


def _seed_exact_failed_context_attempt(
    *,
    parent_id: str,
    quote_id: str,
    reply_text: str,
    source_attempt_number: int = 1,
):
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_id,
        main_post_confirmed_epoch=8_000,
        quote_id=quote_id,
        quote_text="A context attempt with exact local failure proof.",
    )
    store.claim_attempt(parent_id, started_epoch=8_100)
    source_receipt = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": parent_id,
        "quote_id": quote_id,
        "reply_text": reply_text,
        "reply_epoch": 1_800_000_000,
        "started_at": "2026-08-01T12:00:00Z",
        "attempt_number": source_attempt_number,
    }
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        source_receipt,
    )
    source_sha256 = _bind_context_attempt_to_source_receipt(
        store,
        parent_id=parent_id,
        outbox_attempt=1,
        source_receipt=source_receipt,
    )
    store.mark_remote_transaction_started(parent_id, attempt_number=1)
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {
            "schema_version": 1,
            "items": {
                parent_id: _proved_failure_history_item(
                    source_receipt=source_receipt,
                    source_sha256=source_sha256,
                )
            },
        },
    )
    return store


def test_startup_retires_failure_receipt_after_outbox_failure_is_durable() -> None:
    """A crash after the outbox write leaves a locally completable receipt."""

    parent_id = "800027"
    store = _seed_exact_failed_context_attempt(
        parent_id=parent_id,
        quote_id="2" * 64,
        reply_text="Context — The durable outbox failure came first.",
    )
    store.record_retryable_failure(
        parent_id,
        attempt_number=1,
        error="RemoteOperationsPaused: paused before transport",
        failed_epoch=8_200,
        proved_remote_non_success=True,
    )

    bot.reconcile_runtime_historical_context_state()

    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert store.get(parent_id)["context_reply"]["state"] == (
        "context_reply_failed_retryable"
    )


def test_startup_rejects_failure_receipt_conflicting_with_confirmed_outbox() -> None:
    """Definite failure proof cannot retire against a confirmed outbox."""

    parent_id = "800028"
    store = _seed_exact_failed_context_attempt(
        parent_id=parent_id,
        quote_id="3" * 64,
        reply_text="Context — Contradictory durable outcomes stay blocked.",
    )
    store.record_confirmed(
        parent_id,
        attempt_number=1,
        reply_post_id="980028",
        confirmed_epoch=8_200,
    )

    with pytest.raises(
        RuntimeError,
        match="failure receipt conflicts with its outbox state",
    ):
        bot.reconcile_runtime_historical_context_state()

    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert store.get(parent_id)["context_reply"]["state"] == (
        "context_reply_confirmed"
    )


@pytest.mark.parametrize("entrypoint", ("startup", "prebarrier"))
def test_failed_context_receipt_without_outbox_obligation_stays_blocked(
    entrypoint: str,
) -> None:
    """Local failure proof alone cannot erase its missing outbox authority."""

    parent_id = "800029"
    quote_id = "4" * 64
    reply_text = "Context — Missing outbox authority remains blocked."
    source_receipt = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": parent_id,
        "quote_id": quote_id,
        "reply_text": reply_text,
        "reply_epoch": 1_800_000_000,
        "started_at": "2026-08-01T12:00:00Z",
        "attempt_number": 1,
    }
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        source_receipt,
    )
    source_sha256 = hashlib.sha256(
        context_formatter.canonical_json_bytes(source_receipt)
    ).hexdigest()
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {
            "schema_version": 1,
            "items": {
                parent_id: _proved_failure_history_item(
                    source_receipt=source_receipt,
                    source_sha256=source_sha256,
                )
            },
        },
    )

    with pytest.raises(
        RuntimeError,
        match="no matching outbox obligation",
    ):
        if entrypoint == "startup":
            bot.reconcile_runtime_historical_context_state()
        else:
            bot.reconcile_confirmed_transactions_before_global_barrier(
                set(),
                set(),
                {},
            )

    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert context_formatter.HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    ).history()["items"][parent_id]["status"] == "failed"


@pytest.mark.parametrize("entrypoint", ("startup", "prebarrier"))
def test_ambiguous_context_sending_without_outbox_remains_ambiguous(
    entrypoint: str,
) -> None:
    """A plain interrupted send is not recast as a proved local failure."""

    parent_id = "800030"
    reply_text = "Context — This send has no proved remote outcome."
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        {
            "schema_version": 1,
            "lifecycle_state": "sending",
            "parent_post_id": parent_id,
            "quote_id": "5" * 64,
            "reply_text": reply_text,
            "reply_epoch": 1_800_000_000,
            "started_at": "2026-08-01T12:00:00Z",
            "attempt_number": 1,
        },
    )

    with pytest.raises(
        context_formatter.AmbiguousContextReplyOutcome,
        match="interrupted while sending",
    ):
        if entrypoint == "startup":
            bot.reconcile_runtime_historical_context_state()
        else:
            bot.reconcile_confirmed_transactions_before_global_barrier(
                set(),
                set(),
                {},
            )

    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert not bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.exists()


def test_legacy_claim_cannot_consume_stale_failed_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A legacy unknown phase stays blocked despite an older failed row."""

    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800021",
        main_post_confirmed_epoch=8_000,
        quote_id="b" * 64,
        quote_text="A legacy later attempt with stale failure history.",
    )
    store.claim_attempt("800021", started_epoch=8_000)
    store.record_retryable_failure(
        "800021",
        attempt_number=1,
        error="older definite failure",
        failed_epoch=8_100,
    )
    store.claim_attempt("800021", started_epoch=8_200)
    document = json.loads(
        bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.read_text(encoding="utf-8")
    )
    document["obligations"]["800021"]["context_reply"].pop(
        "remote_transaction_started"
    )
    outbox_module._atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE,
        document,
    )
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {
            "schema_version": 1,
            "items": {
                "800021": {
                    "parent_post_id": "800021",
                    "quote_id": "b" * 64,
                    "reply_text": "Rendered context text.",
                    "status": "failed",
                    "failure": "TimeoutError: older first attempt",
                    "attempt_count": 1,
                    "updated_at": "2026-08-01T00:00:00Z",
                }
            },
        },
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_300)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("repeating legacy work with only stale failure history"),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800021",
    )

    assert result == [
        {
            "parent_post_id": "800021",
            "status": "interrupted_attempt_recovery_failed",
            "error_type": "AmbiguousContextReplyOutcome",
        }
    ]
    assert (
        store.get("800021")["context_reply"]["state"]
        == "context_reply_attempting"
    )
    assert bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()


def test_pre_remote_claim_cannot_consume_stale_failure_over_transport_journal() -> None:
    """Independent transport evidence overrides an explicit false phase."""

    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800022",
        main_post_confirmed_epoch=8_000,
        quote_id="c" * 64,
        quote_text="A pre-remote claim with stale history and a journal.",
    )
    store.claim_attempt("800022", started_epoch=8_000)
    store.record_retryable_failure(
        "800022",
        attempt_number=1,
        error="older definite failure",
        failed_epoch=8_100,
    )
    claimed = store.claim_attempt("800022", started_epoch=8_200)
    assert claimed["context_reply"]["remote_transaction_started"] is False
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {
            "schema_version": 1,
            "items": {
                "800022": {
                    "parent_post_id": "800022",
                    "quote_id": "c" * 64,
                    "reply_text": "Rendered context text.",
                    "status": "failed",
                    "failure": "TimeoutError: older first attempt",
                    "attempt_count": 1,
                    "updated_at": "2026-08-01T00:00:00Z",
                }
            },
        },
    )
    journal_path = bot.journal_path_for_receipt(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
    )
    journal_path.write_bytes(b"invalid but blocking journal namespace\n")

    with pytest.raises(
        context_formatter.AmbiguousContextReplyOutcome,
        match="only an older failed history outcome",
    ):
        bot.recover_interrupted_historical_context_attempt(
            store,
            store.get("800022"),
            recovered_epoch=8_300,
        )

    assert (
        store.get("800022")["context_reply"]["state"]
        == "context_reply_attempting"
    )
    assert journal_path.exists()


def test_interrupted_claim_reconciles_completed_history_without_reposting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800009",
        main_post_confirmed_epoch=8_000,
        quote_id="8" * 64,
        quote_text="A context reply already confirmed before interruption.",
    )
    store.claim_attempt("800009", started_epoch=8_100)
    source_receipt = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": "800009",
        "quote_id": "8" * 64,
        "reply_text": "Rendered context text.",
        "reply_epoch": 1_800_000_101,
        "started_at": "2026-07-24T00:00:00Z",
        "attempt_number": 1,
    }
    _bind_context_attempt_to_source_receipt(
        store,
        parent_id="800009",
        outbox_attempt=1,
        source_receipt=source_receipt,
    )
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {
            "schema_version": 1,
            "items": {
                "800009": {
                    **source_receipt,
                    "lifecycle_state": "confirmed",
                    "reply_post_id": "900009",
                    "confirmed_at": "2026-07-24T00:00:01Z",
                    "source_receipt_sha256": hashlib.sha256(
                        context_formatter.canonical_json_bytes(source_receipt)
                    ).hexdigest(),
                    "status": "completed",
                }
            },
        },
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_200)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("reposting an already confirmed context reply"),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800009",
    )

    assert result[0]["status"] == "recovered_confirmed_history"
    assert result[0]["remote_work_repeated"] is False
    confirmed = store.get("800009")["context_reply"]
    assert confirmed["state"] == "context_reply_confirmed"
    assert confirmed["reply_post_id"] == "900009"


def test_prebarrier_reaches_source_bound_completed_context_without_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash after receipt retirement cannot strand confirmed outbox work."""

    parent_id = "800059"
    quote_id = "d" * 64
    source_receipt = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": parent_id,
        "quote_id": quote_id,
        "reply_text": "Context — Exact completed history survived the crash.",
        "reply_epoch": 1_800_000_501,
        "started_at": "2026-08-01T12:08:20Z",
        "attempt_number": 1,
    }
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_id,
        main_post_confirmed_epoch=1_800_000_400,
        quote_id=quote_id,
        quote_text="A confirmed reply whose outbox update was interrupted.",
    )
    store.claim_attempt(parent_id, started_epoch=1_800_000_500)
    source_sha256 = _bind_context_attempt_to_source_receipt(
        store,
        parent_id=parent_id,
        outbox_attempt=1,
        source_receipt=source_receipt,
    )
    store.mark_remote_transaction_started(parent_id, attempt_number=1)
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {
            "schema_version": 1,
            "items": {
                parent_id: {
                    **source_receipt,
                    "lifecycle_state": "confirmed",
                    "reply_post_id": "900059",
                    "confirmed_at": "2026-08-01T12:08:21Z",
                    "source_receipt_sha256": source_sha256,
                    "status": "completed",
                }
            },
        },
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_502)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("remote work during pre-barrier context recovery"),
    )

    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert bot.historical_context_outbox_remote_attempt_is_blocking() is True

    recovered = bot.reconcile_confirmed_transactions_before_global_barrier(
        set(),
        set(),
        {},
    )

    assert recovered == {
        "historical_context": True,
        "conversational_reply": False,
        "regular": False,
        "meme": False,
    }
    context = store.get(parent_id)["context_reply"]
    assert context["state"] == "context_reply_confirmed"
    assert context["reply_post_id"] == "900059"
    assert bot.historical_context_outbox_remote_attempt_is_blocking() is False

    # The completed local transition is idempotent and cannot lend authority
    # to another outbox item or remote lane on a later daemon tick.
    assert bot.reconcile_confirmed_transactions_before_global_barrier(
        set(),
        set(),
        {},
    ) == {
        "historical_context": False,
        "conversational_reply": False,
        "regular": False,
        "meme": False,
    }


def test_prebarrier_refuses_to_choose_between_multiple_risky_context_rows() -> None:
    """Local recovery cannot weaken the global barrier by choosing a parent."""

    store = bot.historical_context_outbox_store()
    for index, parent_id in enumerate(("800060", "800061"), start=1):
        store.enqueue(
            parent_id,
            main_post_confirmed_epoch=1_800_000_600 + index,
            quote_id=str(index) * 64,
            quote_text=f"Risky historical-context row {index}.",
        )
        store.claim_attempt(parent_id, started_epoch=1_800_000_610 + index)
        store.bind_attempt_source_receipt(
            parent_id,
            attempt_number=1,
            source_receipt_sha256=("a" if index == 1 else "b") * 64,
            source_receipt_attempt_number=1,
        )
        store.mark_remote_transaction_started(parent_id, attempt_number=1)

    assert bot.reconcile_confirmed_transactions_before_global_barrier(
        set(),
        set(),
        {},
    ) == {
        "historical_context": False,
        "conversational_reply": False,
        "regular": False,
        "meme": False,
    }
    assert bot.historical_context_outbox_remote_attempt_is_blocking() is True
    assert {
        store.get("800060")["context_reply"]["state"],
        store.get("800061")["context_reply"]["state"],
    } == {"context_reply_attempting"}


def test_interrupted_claim_rejects_stale_completed_history_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed row for another source receipt cannot resolve this claim."""

    parent_id = "800034"
    quote_id = "9" * 64
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_id,
        main_post_confirmed_epoch=8_000,
        quote_id=quote_id,
        quote_text="A later attempt with stale completed history.",
    )
    store.claim_attempt(parent_id, started_epoch=8_100)
    store.bind_attempt_source_receipt(
        parent_id,
        attempt_number=1,
        source_receipt_sha256="c" * 64,
        source_receipt_attempt_number=1,
    )
    store.mark_remote_transaction_started(parent_id, attempt_number=1)
    stale_source = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": parent_id,
        "quote_id": quote_id,
        "reply_text": "Rendered stale context.",
        "reply_epoch": 1_800_000_101,
        "started_at": "2026-08-01T00:00:00Z",
        "attempt_number": 1,
    }
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {
            "schema_version": 1,
            "items": {
                parent_id: {
                    **stale_source,
                    "lifecycle_state": "confirmed",
                    "reply_post_id": "900034",
                    "confirmed_at": "2026-08-01T00:00:01Z",
                    "source_receipt_sha256": hashlib.sha256(
                        context_formatter.canonical_json_bytes(stale_source)
                    ).hexdigest(),
                    "status": "completed",
                }
            },
        },
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_200)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("reposting from stale completed history"),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id=parent_id,
    )

    assert result[0]["status"] == "interrupted_attempt_recovery_failed"
    assert result[0]["error_type"] == "AmbiguousContextReplyOutcome"
    assert store.get(parent_id)["context_reply"]["state"] == (
        "context_reply_attempting"
    )


def test_interrupted_claim_accepts_completed_history_with_independent_ordinal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800011",
        main_post_confirmed_epoch=8_000,
        quote_id="a" * 64,
        quote_text="A confirmed reply after one preparation-only failure.",
    )
    store.claim_attempt("800011", started_epoch=8_000)
    store.record_retryable_failure(
        "800011",
        attempt_number=1,
        error="preparation failed before reply-store transaction",
        failed_epoch=8_100,
    )
    store.claim_attempt("800011", started_epoch=8_200)
    source_receipt = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": "800011",
        "quote_id": "a" * 64,
        "reply_text": "Rendered context text.",
        "reply_epoch": 1_800_000_201,
        "started_at": "2026-07-24T00:00:00Z",
        "attempt_number": 1,
    }
    _bind_context_attempt_to_source_receipt(
        store,
        parent_id="800011",
        outbox_attempt=2,
        source_receipt=source_receipt,
    )
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {
            "schema_version": 1,
            "items": {
                "800011": {
                    **source_receipt,
                    "lifecycle_state": "confirmed",
                    "reply_post_id": "900011",
                    "confirmed_at": "2026-07-24T00:00:01Z",
                    "source_receipt_sha256": hashlib.sha256(
                        context_formatter.canonical_json_bytes(source_receipt)
                    ).hexdigest(),
                    "status": "completed",
                }
            },
        },
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_300)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("reposting a confirmed context reply"),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800011",
    )

    assert result[0]["status"] == "recovered_confirmed_history"
    confirmed = store.get("800011")["context_reply"]
    assert confirmed["state"] == "context_reply_confirmed"
    assert confirmed["attempt_count"] == 2
    assert confirmed["reply_post_id"] == "900011"


def test_auxiliary_worker_exception_isolated_after_main_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        bot,
        "process_due_historical_context_obligations",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("injected auxiliary worker fault")
        ),
    )
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda event, **fields: events.append((event, fields)),
    )

    result = bot.safely_process_due_historical_context_obligations(
        parent_post_id="800012",
    )

    assert result == [
        {
            "parent_post_id": "800012",
            "status": "worker_failed_isolated",
            "error_type": "RuntimeError",
        }
    ]
    assert bot._HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON
    assert events == [
        (
            "historical_context_outbox",
            {
                "status": "worker_failed_isolated",
                "parent_post_id": "800012",
                "error_type": "RuntimeError",
                "reason": "injected auxiliary worker fault",
                "main_post_success_preserved": True,
            },
        )
    ]


def test_context_outbox_honours_reply_pause_and_write_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800006",
        main_post_confirmed_epoch=8_000,
        quote_id="5" * 64,
        quote_text="A control-aware context attempt.",
    )
    before = store.snapshot()
    monkeypatch.setattr(bot, "now_epoch", lambda: 9_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *_keys: True)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        _forbid("context attempt while reply controls are paused"),
    )
    assert bot.process_due_historical_context_obligations(
        runtime_state={},
    ) == []
    assert store.snapshot() == before

    monkeypatch.setattr(bot, "lane_paused", lambda *_keys: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: True)
    assert bot.process_due_historical_context_obligations(
        runtime_state={"x_write_api_cooldown_until_epoch": 10_000},
    ) == []
    assert store.snapshot() == before


def test_context_rate_limit_updates_shared_write_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "800007",
        main_post_confirmed_epoch=8_000,
        quote_id="6" * 64,
        quote_text="A rate-limited context attempt.",
    )
    runtime_state: dict = {}
    saved: list[dict] = []
    monkeypatch.setattr(bot, "now_epoch", lambda: 9_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *_keys: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **_kwargs: {
            "status": "failed",
            "error": "X rate limit",
            "error_type": "ApiError",
            "error_service": "x",
            "error_status_code": 429,
            "error_reset_epoch": 9_500,
        },
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda state, **_kwargs: saved.append(copy.deepcopy(state)),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="800007",
        runtime_state=runtime_state,
    )

    assert result[0]["context_reply_state"] == "context_reply_failed_retryable"
    assert runtime_state["x_write_api_cooldown_until_epoch"] == 9_560
    assert "429" in runtime_state["x_write_api_cooldown_reason"]
    assert saved


@pytest.mark.parametrize(
    ("parent_post_id", "message", "status", "path", "expected_state", "error_count"),
    [
        (
            "800008",
            "You attempted to reply to a Tweet that is deleted or not visible to you.",
            403,
            None,
            "context_reply_failed_terminal",
            0,
        ),
        (
            "800009",
            "This application is not permitted to perform that operation.",
            403,
            "/2/tweets",
            "context_reply_failed_retryable",
            1,
        ),
        (
            "800010",
            "endpoint not found",
            404,
            "/2/unsupported",
            "context_reply_failed_retryable",
            1,
        ),
    ],
)
def test_context_outbox_uses_endpoint_aware_terminal_classification(
    monkeypatch: pytest.MonkeyPatch,
    parent_post_id: str,
    message: str,
    status: int,
    path: str | None,
    expected_state: str,
    error_count: int,
) -> None:
    store = bot.historical_context_outbox_store()
    store.enqueue(
        parent_post_id,
        main_post_confirmed_epoch=8_000,
        quote_id="7" * 64,
        quote_text="An endpoint-aware context attempt.",
    )
    runtime_state = bot.default_state()
    monkeypatch.setattr(bot, "now_epoch", lambda: 9_000)
    monkeypatch.setattr(bot, "lane_paused", lambda *_keys: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **_kwargs: {
            "status": "failed",
            "error": message,
            "error_type": "ApiError",
            "error_service": "x",
            "error_status_code": status,
            **(
                {
                    "error_request_method": "POST",
                    "error_request_path": path,
                }
                if path is not None
                else {}
            ),
        },
    )
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    result = bot.process_due_historical_context_obligations(
        parent_post_id=parent_post_id,
        runtime_state=runtime_state,
    )

    assert result[0]["context_reply_state"] == expected_state
    assert len(runtime_state["x_write_error_epochs"]) == error_count


def test_bootstrap_refuses_ambiguous_context_sending_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        {"schema_version": 1, "items": {}},
    )
    bot.historical_context_outbox_store().initialise_empty()
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        {
            "schema_version": 1,
            "lifecycle_state": "sending",
            "parent_post_id": "830001",
            "quote_id": "2" * 64,
            "reply_text": "Context reply text.",
            "reply_epoch": 1_800_000_300,
            "started_at": "2026-07-23T20:00:00Z",
            "attempt_number": 1,
        },
    )
    monkeypatch.setattr(bot, "TEST_MODE", False)
    monkeypatch.setattr(bot, "SELF_TEST_REQUESTED", False)
    monkeypatch.setattr(bot, "INITIALISE_REQUESTED", False)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", False)
    monkeypatch.setattr(bot, "setup_logging", lambda **_kwargs: bot.log)
    monkeypatch.setattr(bot, "apply_local_config", lambda: None)
    monkeypatch.setattr(bot, "validate_runtime_config_values", lambda _values: [])
    monkeypatch.setattr(
        bot,
        "load_completed_research_quote_hashes",
        lambda: {"3" * 64},
    )
    monkeypatch.setattr(bot, "initialise_quote_image_semantic_veto_shadow", lambda: None)
    monkeypatch.setattr(bot, "validate_production_credentials", lambda: None)
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": False},
    )

    bot.production_bootstrap(configure_file_logging=False)
    assert bot._PRODUCTION_BOOTSTRAPPED is True
    with pytest.raises(
        context_formatter.AmbiguousContextReplyOutcome,
        match="interrupted while sending",
    ):
        bot.reconcile_runtime_historical_context_state()

    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert not bot.STATE_FILE.exists()


def test_context_transport_source_requires_private_receipt_mode() -> None:
    receipt = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": "830009",
        "quote_id": "a" * 64,
        "reply_text": "Context reply text.",
        "reply_epoch": 1_800_000_300,
        "started_at": "2026-07-23T20:00:00Z",
        "attempt_number": 1,
    }
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        receipt,
    )
    bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.chmod(0o644)

    assert bot.exact_historical_context_sending_receipt_matches(receipt) is False


def test_interrupted_outbox_claim_recovers_unbound_pretransport_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply_text = "Context — A reply published before its binding callback."
    store = bot.historical_context_outbox_store()
    store.enqueue(
        "830002",
        main_post_confirmed_epoch=8_300,
        quote_id="4" * 64,
        quote_text="The quotation attached to the pre-transport context reply.",
    )
    store.claim_attempt("830002", started_epoch=8_301)
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        {
            "schema_version": 1,
            "lifecycle_state": "sending",
            "parent_post_id": "830002",
            "quote_id": "4" * 64,
            "reply_text": reply_text,
            "reply_epoch": 1_800_000_301,
            "started_at": "2026-07-23T20:00:00Z",
            "attempt_number": 1,
        },
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_302)

    result = bot.process_due_historical_context_obligations(
        parent_post_id="830002",
    )

    assert result[0]["status"] == "recovered_pre_remote_interruption"
    context = store.get("830002")["context_reply"]
    assert context["state"] == "context_reply_failed_retryable"
    assert context["failure"]["remote_outcome"] == "proved_non_success"
    assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert not bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
    assert bot.process_due_historical_context_obligations(
        parent_post_id="830002",
    ) == []


def test_observed_context_receipt_disappearance_remains_globally_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Losing the observed receipt cannot downgrade the attempt to retryable."""

    from historical_context_formatter import HistoricalContextReplyStore

    store = bot.historical_context_outbox_store()
    store.enqueue(
        "830007",
        main_post_confirmed_epoch=8_300,
        quote_id="9" * 64,
        quote_text="The receipt must remain the authoritative barrier.",
    )
    store.claim_attempt("830007", started_epoch=8_301)
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        {
            "schema_version": 1,
            "lifecycle_state": "sending",
            "parent_post_id": "830007",
            "quote_id": "9" * 64,
            "reply_text": "Context — The outcome remains unknown.",
            "reply_epoch": 1_800_000_301,
            "started_at": "2026-07-23T20:00:00Z",
            "attempt_number": 1,
        },
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_302)

    def disappear_after_observation(_self):
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.unlink()
        return None

    monkeypatch.setattr(
        HistoricalContextReplyStore,
        "_load_receipt_safely",
        disappear_after_observation,
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="830007",
    )

    assert result[0]["status"] == "interrupted_attempt_recovery_failed"
    assert result[0]["error_type"] == "AmbiguousContextReplyOutcome"
    assert (
        store.get("830007")["context_reply"]["state"]
        == "context_reply_attempting"
    )
    assert bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE.exists()
    assert bot.ambiguous_remote_post_is_blocking() is True


def test_context_receipt_reconciliation_exception_cannot_claim_another_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker may inspect its old attempt, never start unrelated work."""

    store = bot.historical_context_outbox_store()
    store.enqueue(
        "830003",
        main_post_confirmed_epoch=8_300,
        quote_id="5" * 64,
        quote_text="A different due historical-context quotation.",
    )
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        {
            "schema_version": 1,
            "lifecycle_state": "sending",
            "parent_post_id": "830002",
            "quote_id": "4" * 64,
            "reply_text": "Context — An unresolved earlier attempt.",
            "reply_epoch": 1_800_000_301,
            "started_at": "2026-07-23T20:00:00Z",
            "attempt_number": 1,
        },
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_302)
    monkeypatch.setattr(
        store,
        "claim_attempt",
        lambda *_args, **_kwargs: pytest.fail(
            "an unresolved context receipt must prevent a new claim"
        ),
    )

    result = bot.process_due_historical_context_obligations(
        parent_post_id="830003",
    )

    assert result == []
    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    assert (
        store.get("830003")["context_reply"]["state"]
        == "context_reply_pending"
    )


def test_confirmed_context_receipt_reconciliation_ends_the_worker_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A locally reconciled receipt cannot lend authority to the next item."""

    store = bot.historical_context_outbox_store()
    for parent_id, quote_id in (("830004", "6" * 64), ("830005", "7" * 64)):
        store.enqueue(
            parent_id,
            main_post_confirmed_epoch=8_300,
            quote_id=quote_id,
            quote_text=f"Historical-context quotation {parent_id}.",
        )
    store.claim_attempt("830004", started_epoch=8_301)
    source_receipt = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": "830004",
        "quote_id": "6" * 64,
        "reply_text": "Context — The first attempt was confirmed.",
        "reply_epoch": 1_800_000_301,
        "started_at": "2026-07-23T19:59:59Z",
        "attempt_number": 1,
    }
    source_sha256 = _bind_context_attempt_to_source_receipt(
        store,
        parent_id="830004",
        outbox_attempt=1,
        source_receipt=source_receipt,
    )
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        {
            **source_receipt,
            "lifecycle_state": "confirmed",
            "reply_post_id": "930004",
            "confirmed_at": "2026-07-23T20:00:00Z",
            "source_receipt_sha256": source_sha256,
        },
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 8_302)
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **_kwargs: pytest.fail(
            "receipt reconciliation cannot authorise the next item"
        ),
    )

    result = bot.process_due_historical_context_obligations(limit=2)

    assert [item["parent_post_id"] for item in result] == ["830004"]
    assert result[0]["status"] == "recovered_confirmed_history"
    assert (
        store.get("830005")["context_reply"]["state"]
        == "context_reply_pending"
    )


def test_symlink_context_receipt_never_enters_local_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The narrow exception must not follow an unsafe receipt namespace."""

    store = bot.historical_context_outbox_store()
    store.enqueue(
        "830006",
        main_post_confirmed_epoch=8_300,
        quote_id="8" * 64,
        quote_text="A pending item behind an unsafe receipt.",
    )
    target = tmp_path / "outside-context-receipt.json"
    target.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "parent_post_id": "830006",
                "reply_post_id": "930006",
                "quote_id": "8" * 64,
                "reply_text": "Context — Unsafe receipt target.",
                "reply_epoch": 1_800_000_301,
                "confirmed_at": "2026-07-23T20:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.symlink_to(target)
    monkeypatch.setattr(
        store,
        "claim_attempt",
        lambda *_args, **_kwargs: pytest.fail(
            "unsafe receipt must prevent worker entry"
        ),
    )

    assert bot.process_due_historical_context_obligations(limit=2) == []
    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.is_symlink()
    assert target.exists()


def test_main_acquires_process_lock_before_context_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    class StopAfterReconcile(RuntimeError):
        pass

    monkeypatch.setattr(bot, "require_production_bootstrap", lambda: None)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: order.append("lock"))

    def stop_after_reconcile() -> None:
        order.append("context_reconcile")
        raise StopAfterReconcile

    monkeypatch.setattr(
        bot,
        "reconcile_runtime_historical_context_state",
        stop_after_reconcile,
    )

    with pytest.raises(StopAfterReconcile):
        bot.main()

    assert order == ["lock", "context_reconcile"]


def test_main_checks_established_namespace_only_after_process_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup authority cannot be derived from a pre-lock namespace view."""

    order: list[str] = []

    class StopAfterReconcile(RuntimeError):
        pass

    monkeypatch.setattr(bot, "require_production_bootstrap", lambda: None)
    monkeypatch.setattr(
        bot,
        "acquire_instance_lock",
        lambda: order.append("lock"),
    )
    monkeypatch.setattr(
        bot,
        "require_established_installation",
        lambda: order.append("installation"),
    )

    def stop_after_reconcile() -> None:
        order.append("context_reconcile")
        raise StopAfterReconcile

    monkeypatch.setattr(
        bot,
        "reconcile_runtime_historical_context_state",
        stop_after_reconcile,
    )

    with pytest.raises(StopAfterReconcile):
        bot.main()

    assert order == ["lock", "installation", "context_reconcile"]


@pytest.mark.parametrize(
    "command_name",
    [
        "run_test_cycle",
        "run_test_main_tick",
        "run_test_post_quote",
        "run_test_post_meme",
    ],
)
def test_one_shot_commands_reconcile_ambiguous_context_receipt_after_lock(
    monkeypatch: pytest.MonkeyPatch,
    command_name: str,
) -> None:
    context_formatter.atomic_write_json(
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        {
            "schema_version": 1,
            "lifecycle_state": "sending",
            "parent_post_id": "830003",
            "quote_id": "5" * 64,
            "reply_text": "Context reply with an unresolved remote outcome.",
            "reply_epoch": 1_800_000_303,
            "started_at": "2026-07-27T19:00:00Z",
            "attempt_number": 1,
        },
    )
    receipt_bytes = bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes()
    order: list[str] = []
    real_reconcile = bot.reconcile_runtime_historical_context_state

    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(
        bot,
        "acquire_instance_lock",
        lambda: order.append("lock"),
    )

    def reconcile_after_lock() -> None:
        order.append("context_reconcile")
        real_reconcile()

    monkeypatch.setattr(
        bot,
        "reconcile_runtime_historical_context_state",
        reconcile_after_lock,
    )
    monkeypatch.setattr(
        bot,
        "load_runtime_state",
        lambda: pytest.fail(
            "one-shot command loaded state despite an ambiguous context receipt"
        ),
    )

    with pytest.raises(
        context_formatter.AmbiguousContextReplyOutcome,
        match="interrupted while sending",
    ):
        getattr(bot, command_name)()

    assert order == ["lock", "context_reconcile"]
    assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes() == receipt_bytes
    assert not bot.STATE_FILE.exists()


def test_corrupt_context_outbox_latches_only_context_and_quote_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.write_text(
        '{"schema_version":1,"broken":true}\n',
        encoding="utf-8",
    )

    bot.reconcile_runtime_historical_context_state()

    assert bot._HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON
    assert bot.run_daily_meme_stage("asset_selection", lambda: "meme") == "meme"
    with pytest.raises(RuntimeError, match="outbox is unavailable"):
        bot.require_historical_context_outbox_writable()


def test_context_runtime_unavailable_does_not_require_outbox_for_main_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnavailableOutbox:
        def verify_writable(self) -> None:
            pytest.fail("context-only outbox should not gate the main post")

        def enqueue(self, *_args, **_kwargs):
            raise OSError("context-only outbox unavailable")

    monkeypatch.setattr(
        bot,
        "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON",
        "RuntimeError: source-role policy mismatch",
    )
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    monkeypatch.setattr(
        bot,
        "historical_context_outbox_store",
        lambda: UnavailableOutbox(),
    )
    bot.require_historical_context_outbox_writable()

    obligation = bot.enqueue_historical_context_obligation(_regular_receipt())

    assert obligation["main_post"]["state"] == "main_post_confirmed"
    assert obligation["context_reply"] == {
        "state": "context_reply_not_required",
        "reason": "historical_context_runtime_unavailable",
        "updated_epoch": 1_800_000_000,
    }
    assert bot._HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON


@pytest.mark.parametrize("recovery_entry", ("sole_main", "global_prebarrier"))
def test_local_recovery_refuses_uninspectable_context_receipt_namespace(
    recovery_entry: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local-only recovery also refuses to guess among uninspectable owners."""

    real_lstat = bot.os.lstat

    def fail_context_receipt(path, *args, **kwargs):
        if Path(path) == bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE:
            raise PermissionError("injected context receipt inspection failure")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(bot.os, "lstat", fail_context_receipt)
    with pytest.raises(PermissionError, match="context receipt inspection"):
        if recovery_entry == "sole_main":
            bot.confirmed_main_receipt_is_sole_local_recovery_barrier()
        else:
            bot.reconcile_confirmed_transactions_before_global_barrier(
                set(),
                set(),
                {},
                current=1_800_000_000,
            )


@pytest.mark.parametrize(
    "existing_name",
    [
        "outbox",
        "outbox_lock",
        "outbox_worker_lock",
        "remote_write_restart_barrier",
    ],
)
def test_installation_initialisation_refuses_existing_durable_state(
    existing_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This test exercises refusal caused by the selected pre-existing durable
    # target.  Remove the fixture's normal active-protocol sentinel so it
    # cannot mask that target-specific refusal.
    bot.REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE.unlink()
    if existing_name == "outbox":
        path = bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE
    elif existing_name == "outbox_lock":
        path = bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.with_name(
            f"{bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.name}.lock"
        )
    elif existing_name == "outbox_worker_lock":
        path = bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.with_name(
            f"{bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.name}.worker.lock"
        )
    else:
        path = bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE
    path.write_text("durable sentinel\n", encoding="utf-8")
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)

    with pytest.raises(RuntimeError, match="Refusing to initialise"):
        bot.initialise_installation()

    assert path.read_text(encoding="utf-8") == "durable sentinel\n"


def test_main_post_preflight_stops_before_x_when_outbox_becomes_unwritable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnwritableOutbox:
        def snapshot(self) -> dict:
            return {"schema_version": 1, "obligations": {}}

        def verify_writable(self) -> None:
            raise OSError("injected outbox write failure")

    monkeypatch.setattr(
        bot,
        "historical_context_outbox_store",
        lambda: UnwritableOutbox(),
    )
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )

    with pytest.raises(RuntimeError, match="pre-post durability check"):
        bot.post_random_quote(set(), set(), {})

    assert bot._HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON
    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
    assert not bot.MEME_POST_RECEIPT_FILE.exists()


def test_meme_failure_stage_preserves_quote_state_and_ignores_context_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbox = bot.historical_context_outbox_store()
    outbox.enqueue(
        "820001",
        main_post_confirmed_epoch=8_200,
        quote_id="c" * 64,
        quote_text="A confirmed quote with terminal context.",
    )
    outbox.claim_attempt("820001", started_epoch=8_200)
    outbox.record_terminal_failure(
        "820001",
        attempt_number=1,
        error="context is terminal",
        failed_epoch=8_201,
    )
    outbox_before = outbox.snapshot()
    class SafetySnapshotOnly:
        def snapshot(self) -> dict:
            return outbox_before

        def __getattr__(self, _name: str):
            return _forbid("daily meme mutation of historical-context outbox")

    monkeypatch.setattr(
        bot,
        "historical_context_outbox_store",
        lambda: SafetySnapshotOnly(),
    )

    quote_state = {
        "last_main_post_id": "820001",
        "last_quote_post_epoch": 8_200,
        "next_quote_post_epoch": 18_200,
        "last_regular_image_filename": "t01.jpg",
        "next_meme_post_epoch": 8_300,
        "posted_meme_filenames": [],
    }
    state_before = copy.deepcopy(quote_state)
    quote_history = b'["quote-history-sentinel"]\n'
    image_history = b'["image-history-sentinel"]\n'
    bot.LINES_USED_FILE.write_bytes(quote_history)
    bot.IMAGES_USED_FILE.write_bytes(image_history)
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda event, **fields: events.append((event, fields)),
    )
    monkeypatch.setattr(
        bot,
        "choose_next_meme",
        lambda _state: (_ for _ in ()).throw(
            RuntimeError("meme asset index unavailable")
        ),
    )

    with pytest.raises(RuntimeError, match="meme asset index unavailable"):
        bot.post_next_meme(quote_state)

    assert events == [
        (
            "daily_meme_failure",
            {
                "status": "failed",
                "stage": "meme_eligibility_and_asset_selection",
                "error_type": "RuntimeError",
                "reason": "meme asset index unavailable",
            },
        )
    ]
    assert quote_state == state_before
    assert bot.LINES_USED_FILE.read_bytes() == quote_history
    assert bot.IMAGES_USED_FILE.read_bytes() == image_history
    assert outbox.snapshot() == outbox_before

    events.clear()
    schedule_calls: list[dict] = []
    monkeypatch.setattr(bot, "choose_next_meme", lambda _state: None)
    monkeypatch.setattr(
        bot,
        "schedule_next_meme_post",
        lambda state: schedule_calls.append(copy.deepcopy(state)),
    )
    bot.post_next_meme(quote_state)

    assert schedule_calls == [state_before]
    assert events == []
    assert quote_state == state_before
    assert outbox.snapshot() == outbox_before
    assert not bot.REGULAR_POST_RECEIPT_FILE.exists()
    assert not bot.MEME_POST_RECEIPT_FILE.exists()

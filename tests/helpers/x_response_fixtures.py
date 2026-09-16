"""Share X response builders, transport authority, and opt-in write isolation."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

import mrsMThatcher2 as bot
import remote_write_transport_journal as journal_module
from tests.helpers.protocol_activation import create_test_protocol_activation
from tests.helpers.reply_fixtures import UNIT_REPLY_REPOSITORY


def _x_response(status_code: int, body: object) -> bot.requests.Response:
    """Create a JSON response with the requested status and payload."""

    response = bot.requests.Response()
    response.status_code = status_code
    response._content = json.dumps(body).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


PRODUCTION_DELETED_REPLY_ERROR = {
    "detail": (
        "You attempted to reply to a Tweet that is deleted or not visible "
        "to you."
    ),
    "status": 403,
    "title": "Forbidden",
    "type": "about:blank",
}


def _existing_reply_target_then_deleted_create(
    target_id: str,
    *,
    remote_calls: list[str] | None = None,
):
    """Return a request stub for a live preflight target and rejected create."""

    target_path = f"/2/tweets/{target_id}"

    def request(
        method: str,
        url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        method = str(method).upper()
        path = urlsplit(str(url)).path
        if remote_calls is not None:
            remote_calls.append(f"{method} {path}")
        if method == "GET" and path == target_path:
            return _x_response(200, {"data": {"id": target_id}})
        if method == "POST" and path == "/2/tweets":
            return _x_response(403, PRODUCTION_DELETED_REPLY_ERROR)
        pytest.fail(f"unexpected X request in reply rejection test: {method} {path}")

    return request


def _raw_x_response(
    status_code: int,
    body: str | bytes,
) -> bot.requests.Response:
    """Create a response with exact bytes for parser boundary tests."""

    response = bot.requests.Response()
    response.status_code = status_code
    response._content = body if isinstance(body, bytes) else body.encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


def _armed_x_create_transaction(
    payload: dict[str, object],
) -> tuple[bot.SourceReceiptBinding, bot.TransportAuthority]:
    """Create one exact journal authority for a direct transport unit test."""

    receipt = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "unit_test": True,
    }
    bot.atomic_write_json(bot.CONFIRMED_REPLY_RECEIPT_FILE, receipt, durable=True)
    source = bot.bind_transport_source(
        receipt_path=bot.CONFIRMED_REPLY_RECEIPT_FILE,
        expected_receipt=receipt,
        lane="conversational_reply",
        payload=payload,
        validator_id="unit-test-source-binding-v2",
        validator=lambda lane, observed, body: bool(
            lane == "conversational_reply"
            and observed == receipt
            and body == payload
        ),
    )
    prepared = bot.begin_transport_transaction(
        receipt_path=bot.CONFIRMED_REPLY_RECEIPT_FILE,
        source_binding=source,
    )
    armed = bot.arm_transport_transaction(
        Path(prepared.journal_path),
        prepared,
        mutation_authority=bot.transaction_mutation_authority(
            "focused transport arming"
        ),
    )
    return source, armed


def _armed_x_create_authority(payload: dict[str, object]) -> bot.TransportAuthority:
    """Return the armed authority for one exact test payload."""

    return _armed_x_create_transaction(payload)[1]


@pytest.fixture(autouse=True)
def isolate_remote_write_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep every durable ambiguity barrier inside one test directory."""
    journal_module.reset_consumed_authorities_for_tests()
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", True)
    monkeypatch.setattr(
        bot,
        "REGULAR_POST_RECEIPT_FILE",
        tmp_path / "regular_post_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "MEME_POST_RECEIPT_FILE",
        tmp_path / "meme_post_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "CONFIRMED_REPLY_RECEIPT_FILE",
        tmp_path / "confirmed_reply_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "MEDIA_UPLOAD_RECEIPT_FILE",
        tmp_path / "remote_media_upload_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "AMBIGUOUS_POST_OUTCOME_FILE",
        tmp_path / "ambiguous_post_outcome.json",
    )
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
    monkeypatch.setattr(bot, "CONTROL_FILE", tmp_path / "mrsMThatcher.control.json")
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    monkeypatch.setattr(bot, "_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN", False)
    monkeypatch.setattr(bot, "_RETAINED_CONFIRMED_POST_SIGINT_GUARD", None)
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
        ),
    )
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": False},
    )
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)

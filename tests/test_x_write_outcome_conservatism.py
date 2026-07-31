from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import mrsMThatcher2 as bot
from historical_context_formatter import (
    AmbiguousContextReplyOutcome,
    HistoricalContextReplyStore,
)
from tests.helpers.protocol_activation import create_test_protocol_activation
from tests.test_unit_helpers import (
    UNIT_REPLY_REPOSITORY,
    configure_simple_meme_post,
    configure_simple_quote_post,
    unit_sending_reply_receipt,
)


def _x_response(status_code: int, body: object) -> bot.requests.Response:
    response = bot.requests.Response()
    response.status_code = status_code
    response._content = json.dumps(body).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


def test_raw_and_bearer_x_create_require_receipt_bound_internal_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No public transport helper can bypass the durable transaction owner."""

    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: pytest.fail(
            "unbound X create must stop before transport"
        ),
    )
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="internal exact-receipt authorization",
    ):
        bot.x_request(
            "POST",
            "/2/tweets",
            json={"text": "unbound"},
            ambiguous_write=True,
        )
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="ambiguous-write handling",
    ):
        bot.x_request(
            "POST",
            "/2/tweets",
            json={"text": "unbound"},
            _remote_write_authorization=(
                bot._REMOTE_WRITE_PREFLIGHT_AUTHORIZATION
            ),
        )
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="no durable transaction authority",
    ):
        bot.x_bearer_request(
            "POST",
            "/2/tweets",
            json={"text": "unbound"},
        )


@pytest.mark.parametrize(
    "path",
    (
        "/2/./tweets",
        "/2/intermediate/../tweets",
        "/%32/tweets",
        "/2/%74weets",
        "/%252532/%252574weets",
        "/2\\tweets",
    ),
)
def test_normalised_raw_and_bearer_x_create_variants_require_authority(
    path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prepared/decoded route aliases cannot bypass receipt authority."""

    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: pytest.fail(
            "normalised unbound X create must stop before transport"
        ),
    )
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="internal exact-receipt authorization",
    ):
        bot.x_request(
            "POST",
            path,
            json={"text": "unbound"},
            ambiguous_write=True,
        )
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="no durable transaction authority",
    ):
        bot.x_bearer_request(
            "POST",
            path,
            json={"text": "unbound"},
        )


def test_legitimate_non_create_x_post_remains_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normalisation hardening does not turn media upload into tweet create."""

    calls: list[tuple[str, str]] = []

    def uploaded(method: str, url: str, **_kwargs: object) -> bot.requests.Response:
        calls.append((method, url))
        return _x_response(200, {"data": {"id": "123"}})

    monkeypatch.setattr(bot.requests, "request", uploaded)
    monkeypatch.setattr(bot, "require_instance_lock_for_remote_write", lambda _op: None)
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: False)

    assert bot.x_request("POST", "/2/media/upload", data={"media_type": "image/jpeg"}) == {
        "data": {"id": "123"}
    }
    assert calls == [("POST", f"{bot.X_BASE}/2/media/upload")]


def test_create_post_requires_exactly_one_prepared_durable_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bot,
        "x_request",
        lambda *_args, **_kwargs: pytest.fail(
            "unbound create_post must stop before X transport"
        ),
    )

    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="exactly one prepared durable transaction receipt",
    ):
        bot.create_post("unbound")


@pytest.fixture(autouse=True)
def isolate_remote_write_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep every durable ambiguity barrier inside one test directory."""
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
        tmp_path / ".mrs_remote_write_safety_protocol_v1",
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


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 408, 409, 425, 429, 500])
def test_x_create_non_success_is_ambiguous_by_default(
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def rejected(
        _method: str,
        _url: str,
        **kwargs: object,
    ) -> bot.requests.Response:
        calls.append(kwargs)
        return _x_response(
            status_code,
            {"errors": [{"detail": "generic response without a no-create proof"}]},
        )

    monkeypatch.setattr(bot.requests, "request", rejected)

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.x_request(
            "POST",
            "/2/tweets",
            json={"text": "unit"},
            ambiguous_write=True,
            _remote_write_authorization=(
                bot._REMOTE_WRITE_PREFLIGHT_AUTHORIZATION
            ),
        )

    assert len(calls) == 1


def test_x_create_redirect_is_not_followed_and_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def redirected(
        _method: str,
        _url: str,
        **kwargs: object,
    ) -> bot.requests.Response:
        calls.append(kwargs)
        response = _x_response(307, {"detail": "redirect"})
        response.headers["Location"] = "https://example.invalid/other-create"
        return response

    monkeypatch.setattr(bot.requests, "request", redirected)

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.x_request(
            "POST",
            "/2/tweets",
            json={"text": "unit"},
            ambiguous_write=True,
            allow_redirects=True,
            _remote_write_authorization=(
                bot._REMOTE_WRITE_PREFLIGHT_AUTHORIZATION
            ),
        )

    assert len(calls) == 1
    assert calls[0]["allow_redirects"] is False


def test_regular_generic_4xx_retains_attempt_and_blocks_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    lines_used, images_used, state, *_paths = configure_simple_quote_post(
        tmp_path,
        monkeypatch,
    )
    quote_hash = bot.quote_text_hash("Good quote.")
    monkeypatch.setattr(bot, "completed_research_quote_hashes", lambda: {quote_hash})
    monkeypatch.setattr(bot, "create_post", actual_create_post)
    remote_calls = 0

    def generic_400(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        nonlocal remote_calls
        remote_calls += 1
        return _x_response(400, {"errors": [{"detail": "generic rejection"}]})

    monkeypatch.setattr(bot.requests, "request", generic_400)

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_random_quote(lines_used, images_used, state)

    status, attempt = bot.load_regular_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lifecycle_state"] == "attempting"
    receipt_bytes = bot.REGULAR_POST_RECEIPT_FILE.read_bytes()
    assert remote_calls == 1

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_random_quote(lines_used, images_used, state)

    assert remote_calls == 1
    assert bot.REGULAR_POST_RECEIPT_FILE.read_bytes() == receipt_bytes


def test_meme_generic_4xx_retains_attempt_and_blocks_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    state, _receipt_path = configure_simple_meme_post(tmp_path, monkeypatch)
    monkeypatch.setattr(bot, "create_post", actual_create_post)
    remote_calls = 0

    def generic_403(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        nonlocal remote_calls
        remote_calls += 1
        return _x_response(403, {"errors": [{"detail": "generic forbidden"}]})

    monkeypatch.setattr(bot.requests, "request", generic_403)

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_next_meme(state)

    status, attempt = bot.load_meme_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lifecycle_state"] == "attempting"
    receipt_bytes = bot.MEME_POST_RECEIPT_FILE.read_bytes()
    assert remote_calls == 1

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_next_meme(state)

    assert remote_calls == 1
    assert bot.MEME_POST_RECEIPT_FILE.read_bytes() == receipt_bytes


def test_conversational_generic_4xx_retains_sending_receipt_and_blocks_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sending = unit_sending_reply_receipt()
    remote_calls = 0

    def generic_404(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        nonlocal remote_calls
        remote_calls += 1
        return _x_response(404, {"errors": [{"detail": "generic not found"}]})

    monkeypatch.setattr(bot.requests, "request", generic_404)

    def invoke() -> tuple[dict, dict]:
        return bot.post_conversational_reply_with_durable_identity(
            state=bot.default_state(),
            receipt_template=sending,
            reply_text=str(sending["reply_text"]),
            reply_to_id=str(sending["target_id"]),
            made_with_ai=False,
            lane="mention",
        )

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        invoke()

    assert bot.load_confirmed_reply_receipt() == ("sending", sending)
    receipt_bytes = bot.CONFIRMED_REPLY_RECEIPT_FILE.read_bytes()
    assert remote_calls == 1

    with pytest.raises(bot.InvalidConfirmedReplyReceipt, match="unresolved"):
        invoke()

    assert remote_calls == 1
    assert bot.CONFIRMED_REPLY_RECEIPT_FILE.read_bytes() == receipt_bytes


def test_historical_context_generic_4xx_retains_sending_receipt_and_blocks_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = HistoricalContextReplyStore(
        tmp_path / "context-history.json",
        tmp_path / "context-receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE",
        store.receipt_path,
    )
    remote_calls = 0

    def generic_409(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        nonlocal remote_calls
        remote_calls += 1
        return _x_response(409, {"errors": [{"detail": "generic conflict"}]})

    monkeypatch.setattr(bot.requests, "request", generic_409)

    def create_context_post(**kwargs: object) -> dict:
        return bot.create_post(**kwargs)

    with pytest.raises(AmbiguousContextReplyOutcome):
        store.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=create_context_post,
            now_epoch=lambda: 123,
        )

    sending = json.loads(store.receipt_path.read_text(encoding="utf-8"))
    assert sending["lifecycle_state"] == "sending"
    receipt_bytes = store.receipt_path.read_bytes()
    assert remote_calls == 1

    with pytest.raises(AmbiguousContextReplyOutcome):
        store.post(
            parent_post_id="111",
            quote_id="a" * 64,
            reply_text="Context",
            create_post=lambda **_kwargs: pytest.fail(
                "restart must not repeat an ambiguous context create"
            ),
            now_epoch=lambda: 124,
        )

    assert remote_calls == 1
    assert store.receipt_path.read_bytes() == receipt_bytes


def test_exact_owning_historical_context_create_is_allowed_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The transaction which durably wrote the exact receipt may send once."""

    store = HistoricalContextReplyStore(
        bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    )
    remote_calls: list[dict[str, object]] = []

    def accepted(
        _method: str,
        _url: str,
        **kwargs: object,
    ) -> bot.requests.Response:
        remote_calls.append(kwargs)
        return _x_response(201, {"data": {"id": "222"}})

    monkeypatch.setattr(bot.requests, "request", accepted)

    result = store.post(
        parent_post_id="111",
        quote_id="a" * 64,
        reply_text="Context — exact transaction owner.",
        create_post=bot.create_post,
        now_epoch=lambda: 1_800_000_000,
    )

    assert result["status"] == "completed"
    assert result["reply_post_id"] == "222"
    assert len(remote_calls) == 1
    assert not store.receipt_path.exists()
    completed = store.history()["items"]["111"]
    assert completed["status"] == "completed"
    assert completed["reply_post_id"] == "222"


def test_prepared_receipt_authority_requires_the_exact_durable_file() -> None:
    """An in-memory receipt cannot authorise a send after its file vanished."""

    conversational = unit_sending_reply_receipt()
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="exact durable sending receipt",
    ):
        bot.block_if_ambiguous_remote_post(
            prepared_conversational_reply_receipt=conversational,
        )

    main_attempt = bot.build_main_post_attempt(
        lane="daily_meme",
        text="",
        media_ids=["123"],
        made_with_ai=False,
        selected_identity={"meme_basename": "unit.png"},
        recovery_plan={"next_schedule_mode": "fallback"},
        attempt_epoch=1_800_000_000,
    )
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="exact durable sending receipt",
    ):
        bot.block_if_ambiguous_remote_post(
            prepared_main_post_attempt=main_attempt,
        )

    historical = {
        "schema_version": 1,
        "lifecycle_state": "sending",
        "parent_post_id": "111",
        "quote_id": "a" * 64,
        "reply_text": "Context",
        "reply_epoch": 1_800_000_000,
        "started_at": "2026-07-31T12:00:00Z",
        "attempt_number": 1,
    }
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="not durably present",
    ):
        bot.block_if_ambiguous_remote_post(
            prepared_historical_context_reply_receipt=historical,
        )


def test_made_with_ai_generic_400_never_triggers_second_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_calls = 0

    def generic_400_with_field_name(
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> bot.requests.Response:
        nonlocal remote_calls
        remote_calls += 1
        return _x_response(
            400,
            {
                "errors": [
                    {
                        "detail": (
                            "The proxy echoed made_with_ai but supplied no "
                            "structured proof that the create was rejected"
                        )
                    }
                ]
            },
        )

    monkeypatch.setattr(bot.requests, "request", generic_400_with_field_name)
    receipt = unit_sending_reply_receipt(text="unit reply")
    bot.write_sending_reply_receipt(receipt)

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.create_post(
            "unit reply",
            reply_to_id=str(receipt["target_id"]),
            made_with_ai=True,
            prepared_conversational_reply_receipt=receipt,
        )

    assert remote_calls == 1
